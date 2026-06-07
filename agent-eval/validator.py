"""Scenario preflight validator: checks feasibility before running evaluation."""

from __future__ import annotations

import json
from typing import Any

from models import Scenario
from pydantic import BaseModel
from tools import ToolSimulator


class PreflightResult(BaseModel):
    valid: bool
    feasible_restaurants: list[str] = []  # IDs of restaurants that satisfy all constraints
    feasible_slots: list[dict] = []  # {restaurant_id, date, time_slot, seats}
    issues: list[str] = []  # human-readable list of problems found
    final_date: str = ""  # the effective date after all time constraints
    effective_constraints: dict[str, Any] = {}  # summary of effective constraint values


def validate_scenario(scenario: Scenario) -> PreflightResult:
    """Validate that at least one feasible solution path exists in the tool world.

    Initializes the ToolSimulator with the scenario's world_seed, then queries
    the database to check if any restaurant can satisfy all final constraints
    (including hidden ones that will be revealed later).
    """
    sim = ToolSimulator(scenario)
    cur = sim.conn.cursor()

    # ── Step 1: Extract effective constraints ──
    # Resolve all constraints to their final values (including hidden ones).
    # Later constraints of the same type override earlier ones.

    effective_date: str | None = None
    effective_headcount: int | None = None
    effective_budget: float | None = None
    need_private_room: bool = False
    dietary_requirements: list[str] = []  # e.g. ["nut_free", "dairy_free"]

    # Determine which constraint IDs must be satisfied
    must_satisfy_ids = set(scenario.expected_outcome.must_satisfy)

    for c in scenario.constraints:
        # For headcount: use the latest one (hidden overrides initial)
        if c.type == "headcount":
            # If must_satisfy specifies a particular headcount constraint, prefer that.
            # Otherwise, hidden headcount overrides non-hidden.
            if must_satisfy_ids and c.id in must_satisfy_ids:
                effective_headcount = int(c.value)
            elif not must_satisfy_ids or effective_headcount is None:
                # If no must_satisfy filter or we haven't found one yet
                if c.hidden and effective_headcount is not None:
                    # hidden overrides the initial
                    effective_headcount = int(c.value)
                elif effective_headcount is None:
                    effective_headcount = int(c.value)

        # For time: hidden time constraint changes the date
        elif c.type == "time":
            if c.hidden and c.value or effective_date is None and c.value:
                effective_date = str(c.value)

        # For budget: use the constraint value
        elif c.type == "budget":
            if c.value is not None:
                effective_budget = float(c.value)

        # For preference: check if private room is required
        elif c.type == "preference":
            if (
                c.check_rule
                and "has_private_room" in c.check_rule
                or c.description
                and ("包间" in c.description or "private_room" in c.description.lower())
            ):
                need_private_room = True

        # For dietary: extract allergen requirements
        elif c.type == "dietary":
            desc_lower = c.description.lower()
            if "坚果" in c.description or "nut" in desc_lower:
                dietary_requirements.append("nut_free")
            if "乳糖" in c.description or "dairy" in desc_lower or "lactose" in desc_lower:
                dietary_requirements.append("dairy_free")
            if "麸质" in c.description or "gluten" in desc_lower:
                dietary_requirements.append("gluten_free")
            if "贝类" in c.description or "shellfish" in desc_lower:
                dietary_requirements.append("shellfish_free")
            # If check_rule specifies something specific
            if c.check_rule:
                # e.g. check_rule: "allergen_free_options contains nut_free"
                for allergen in ["nut_free", "dairy_free", "gluten_free", "shellfish_free"]:
                    if allergen in c.check_rule and allergen not in dietary_requirements:
                        dietary_requirements.append(allergen)

    # If no headcount constraint found at all, default to 1
    if effective_headcount is None:
        effective_headcount = 1

    # If no explicit date from time constraints, look for one in the availability data
    if effective_date is None:
        # Use the first date available in the database
        cur.execute("SELECT DISTINCT date FROM availability ORDER BY date LIMIT 1")
        row = cur.fetchone()
        if row:
            effective_date = row["date"]
        else:
            return PreflightResult(
                valid=False,
                issues=["No availability data in database at all"],
                final_date="",
                effective_constraints={},
            )

    # Build effective constraints summary
    effective_constraints: dict[str, Any] = {
        "date": effective_date,
        "headcount": effective_headcount,
    }
    if effective_budget is not None:
        effective_constraints["budget"] = effective_budget
    if need_private_room:
        effective_constraints["private_room"] = True
    if dietary_requirements:
        effective_constraints["dietary"] = dietary_requirements

    # ── Step 2: Query database for feasible restaurants ──
    issues: list[str] = []
    feasible_restaurants: list[str] = []
    feasible_slots: list[dict] = []

    # Get all restaurants
    cur.execute("SELECT * FROM restaurants")
    all_restaurants = [dict(row) for row in cur.fetchall()]

    if not all_restaurants:
        return PreflightResult(
            valid=False,
            issues=["No restaurants in database"],
            final_date=effective_date,
            effective_constraints=effective_constraints,
        )

    for restaurant in all_restaurants:
        rid = restaurant["id"]
        rname = restaurant["name"]
        restaurant_issues: list[str] = []

        # Check budget constraint
        if effective_budget is not None:
            if restaurant["price_per_person"] > effective_budget:
                restaurant_issues.append(
                    f"{rname}({rid}): price {restaurant['price_per_person']} > budget {effective_budget}"
                )
                continue  # skip this restaurant entirely

        # Check private room constraint
        if need_private_room:
            if not restaurant["has_private_room"]:
                restaurant_issues.append(f"{rname}({rid}): no private room")
                continue

        # Check dietary constraints
        allergen_options = json.loads(restaurant["allergen_free_options"])
        dietary_ok = True
        for req in dietary_requirements:
            if req not in allergen_options:
                restaurant_issues.append(
                    f"{rname}({rid}): missing allergen option '{req}', has {allergen_options}"
                )
                dietary_ok = False
                break
        if not dietary_ok:
            continue

        # Check availability on the effective date
        cur.execute(
            "SELECT * FROM availability WHERE restaurant_id = ? AND date = ? AND seats_available >= ?",
            (rid, effective_date, effective_headcount),
        )
        slots = [dict(row) for row in cur.fetchall()]

        if not slots:
            restaurant_issues.append(
                f"{rname}({rid}): no slots with >= {effective_headcount} seats on {effective_date}"
            )
            # Don't add to feasible, but track the issue
            if restaurant_issues:
                issues.extend(restaurant_issues)
            continue

        # This restaurant is feasible!
        feasible_restaurants.append(rid)
        for slot in slots:
            feasible_slots.append(
                {
                    "restaurant_id": rid,
                    "date": slot["date"],
                    "time_slot": slot["time_slot"],
                    "seats": slot["seats_available"],
                }
            )

    # ── Step 3: Build result ──
    if not feasible_restaurants:
        # Collect all issues as to why no restaurant works
        if not issues:
            issues.append(
                f"No restaurant satisfies all constraints: "
                f"date={effective_date}, headcount={effective_headcount}, "
                f"budget={effective_budget}, private_room={need_private_room}, "
                f"dietary={dietary_requirements}"
            )
        return PreflightResult(
            valid=False,
            feasible_restaurants=[],
            feasible_slots=[],
            issues=issues,
            final_date=effective_date,
            effective_constraints=effective_constraints,
        )

    return PreflightResult(
        valid=True,
        feasible_restaurants=feasible_restaurants,
        feasible_slots=feasible_slots,
        issues=[],
        final_date=effective_date,
        effective_constraints=effective_constraints,
    )
