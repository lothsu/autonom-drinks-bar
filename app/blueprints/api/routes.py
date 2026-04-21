import json
from flask import jsonify, request, current_app
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.drink import Drink
from app.models.member import Member
from app.models.transaction import Transaction


def _err(msg, code=400):
    return jsonify({"error": msg}), code


# ------------------------------------------------------------------
# Drinks
# ------------------------------------------------------------------

@api_bp.get("/drinks")
def get_drinks():
    drinks = Drink.query.filter_by(available=True).order_by(Drink.position).all()
    return jsonify([d.to_dict() for d in drinks])


# ------------------------------------------------------------------
# RFID
# ------------------------------------------------------------------

@api_bp.get("/rfid/scan")
def rfid_scan():
    """Return the last scanned UID (kiosk polls this)."""
    from app import rfid_service
    uid, read_at = rfid_service.get_last_scan()
    if uid is None:
        return jsonify({"uid": None})
    return jsonify({"uid": uid, "read_at": read_at.isoformat()})


@api_bp.post("/rfid/clear")
def rfid_clear():
    from app import rfid_service
    rfid_service.clear()
    return jsonify({"ok": True})


@api_bp.post("/rfid/inject")
def rfid_inject():
    """Dev/mock only: simulate a card tap."""
    if not current_app.config.get("RFID_MOCK"):
        return _err("Only available in mock mode", 403)
    uid = request.json.get("uid")
    if not uid:
        return _err("uid required")
    from app import rfid_service
    rfid_service.inject_uid(uid)
    return jsonify({"ok": True})


# ------------------------------------------------------------------
# Checkout
# ------------------------------------------------------------------

@api_bp.post("/checkout")
def checkout():
    """
    Body: { "rfid_uid": "...", "cart": [{"drink_id": 1, "qty": 2}, ...] }
    """
    data = request.json or {}
    rfid_uid = data.get("rfid_uid")
    cart = data.get("cart", [])

    if not rfid_uid:
        return _err("rfid_uid required")
    if not cart:
        return _err("cart is empty")

    member = Member.query.filter_by(rfid_uid=rfid_uid, active=True).first()
    if not member:
        return _err("Karte nicht berechtigt", 403)

    # build items, calculate total
    items = []
    total_cents = 0
    for entry in cart:
        drink = Drink.query.get(entry.get("drink_id"))
        if not drink or not drink.available:
            return _err(f"Getränk {entry.get('drink_id')} nicht verfügbar")
        qty = int(entry.get("qty", 1))
        line_total = drink.price_cents * qty
        total_cents += line_total
        items.append({
            "drink_id": drink.id,
            "name": drink.name,
            "qty": qty,
            "price_cents": drink.price_cents,
            "line_total_cents": line_total,
        })

    if member and member.balance_cents < total_cents:
        return _err("Guthaben nicht ausreichend", 402)

    if member:
        member.balance_cents -= total_cents

    tx = Transaction(
        rfid_uid=rfid_uid,
        total_cents=total_cents,
        items_json=json.dumps(items),
    )
    db.session.add(tx)
    db.session.commit()

    # clear RFID after successful checkout
    from app import rfid_service
    rfid_service.clear()

    response = {
        "ok": True,
        "transaction_id": tx.id,
        "total": total_cents / 100,
        "known_member": member is not None,
    }
    if member:
        response["new_balance"] = member.balance_cents / 100
    return jsonify(response)


# ------------------------------------------------------------------
# Member lookup
# ------------------------------------------------------------------

@api_bp.get("/member/by-rfid/<uid>")
def member_by_rfid(uid):
    member = Member.query.filter_by(rfid_uid=uid, active=True).first()
    if not member:
        return _err("Not found", 404)
    return jsonify(member.to_dict())


@api_bp.get("/member/by-rfid/<uid>/transactions")
def member_transactions(uid):
    """Return last 5 transactions for a card UID."""
    member = Member.query.filter_by(rfid_uid=uid, active=True).first()
    txs = (Transaction.query
           .filter_by(rfid_uid=uid)
           .order_by(Transaction.created_at.desc())
           .limit(5)
           .all())
    if not member and not txs:
        return _err("Not found", 404)
    return jsonify({"transactions": [t.to_dict() for t in txs]})


@api_bp.put("/transaction/<int:tx_id>")
def update_transaction(tx_id):
    """
    Edit a transaction's items. Recalculates total and adjusts member balance.
    Body: { "rfid_uid": "...", "items": [{"drink_id": 1, "qty": 2}, ...] }
    Items with qty 0 are removed. rfid_uid is used to verify ownership.
    """
    data = request.json or {}
    rfid_uid = data.get("rfid_uid")
    new_items_req = data.get("items", [])

    if not rfid_uid:
        return _err("rfid_uid required")

    tx = Transaction.query.get(tx_id)
    if not tx or tx.rfid_uid != rfid_uid:
        return _err("Transaction not found", 404)
    if not tx.is_editable():
        return _err("Buchung kann nicht mehr bearbeitet werden", 403)

    # Build new items list (skip qty <= 0)
    new_items = []
    new_total_cents = 0
    for entry in new_items_req:
        qty = int(entry.get("qty", 0))
        if qty <= 0:
            continue
        drink = Drink.query.get(entry.get("drink_id"))
        if not drink:
            return _err(f"Drink {entry.get('drink_id')} not found")
        line_total = drink.price_cents * qty
        new_total_cents += line_total
        new_items.append({
            "drink_id": drink.id,
            "name": drink.name,
            "qty": qty,
            "price_cents": drink.price_cents,
            "line_total_cents": line_total,
        })

    if not new_items:
        return _err("Transaction must have at least one item")

    diff_cents = new_total_cents - tx.total_cents
    tx.items_json = json.dumps(new_items)
    tx.total_cents = new_total_cents
    tx.synced = False

    # Adjust member balance if the card belongs to a registered member
    member = Member.query.filter_by(rfid_uid=rfid_uid, active=True).first()
    if member:
        if diff_cents > 0 and member.balance_cents < diff_cents:
            return _err("Guthaben nicht ausreichend", 402)
        member.balance_cents -= diff_cents

    db.session.commit()

    response = {"ok": True, "transaction_id": tx.id, "new_total": new_total_cents / 100}
    if member:
        response["new_balance"] = member.balance_cents / 100
    return jsonify(response)
