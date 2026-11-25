from datetime import datetime, timezone
import json
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import User, get_current_user
from app.database import Base, get_db

router = APIRouter(prefix="/ops", tags=["Transit Operations"])


# ==================== DATABASE MODELS ====================
class CommuterWallet(Base):
    __tablename__ = "commuter_wallets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    balance_zar = Column(Float, default=145.0, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    tx_type = Column(String(24), nullable=False)  # "TOPUP" | "TICKET_PURCHASE"
    title = Column(String(160), nullable=False)
    amount_zar = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BoardingTicket(Base):
    __tablename__ = "boarding_tickets"

    id = Column(Integer, primary_key=True, index=True)
    ticket_code = Column(String(48), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    route_code = Column(String(16), nullable=False)
    route_name = Column(String(140), nullable=False)
    fare_zar = Column(Float, nullable=False)
    status = Column(String(24), default="ACTIVE", nullable=False)  # "ACTIVE" | "VALIDATED"
    issued_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    validated_at = Column(DateTime(timezone=True), nullable=True)


class FleetIncident(Base):
    __tablename__ = "fleet_incidents"

    id = Column(Integer, primary_key=True, index=True)
    route_code = Column(String(16), index=True, nullable=False)
    vehicle_plate = Column(String(32), nullable=False)
    reporter_name = Column(String(120), nullable=False)
    severity = Column(String(24), default="Moderate", nullable=False)  # "Moderate" | "Heavy"
    delay_minutes = Column(Integer, default=5, nullable=False)
    description = Column(String(280), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ==================== SCHEMAS ====================
class TopUpRequest(BaseModel):
    amount_zar: float = Field(..., gt=0, le=5000)


class BuyTicketRequest(BaseModel):
    route_code: str = Field(..., min_length=2, max_length=16)
    route_name: str = Field(..., min_length=2, max_length=140)
    fare_zar: float = Field(..., gt=0)


class ValidateTicketRequest(BaseModel):
    ticket_code: str = Field(..., min_length=4, max_length=48)


class CreateIncidentRequest(BaseModel):
    route_code: str = Field(..., min_length=2, max_length=16)
    vehicle_plate: str = Field(..., min_length=2, max_length=32)
    severity: str = Field(default="Heavy", pattern="^(Moderate|Heavy)$")
    delay_minutes: int = Field(default=6, ge=1, le=90)
    description: str = Field(..., min_length=3, max_length=280)


# ==================== WEBSOCKET & EVENT HUB ====================
class LiveEventHub:
    def __init__(self):
        self.trip_sockets: dict[int, list[WebSocket]] = {}
        self.event_sockets: list[WebSocket] = []

    async def connect_trip(self, trip_id: int, websocket: WebSocket):
        await websocket.accept()
        self.trip_sockets.setdefault(trip_id, []).append(websocket)

    def disconnect_trip(self, trip_id: int, websocket: WebSocket):
        if trip_id in self.trip_sockets:
            self.trip_sockets[trip_id] = [w for w in self.trip_sockets[trip_id] if w != websocket]

    async def broadcast_trip(self, trip_id: int, payload: dict):
        message = json.dumps(payload)
        dead = []
        for ws in self.trip_sockets.get(trip_id, []):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_trip(trip_id, ws)

    async def connect_events(self, websocket: WebSocket):
        await websocket.accept()
        self.event_sockets.append(websocket)

    def disconnect_events(self, websocket: WebSocket):
        self.event_sockets = [w for w in self.event_sockets if w != websocket]

    async def broadcast_event(self, payload: dict):
        message = json.dumps(payload)
        dead = []
        for ws in self.event_sockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_events(ws)


hub = LiveEventHub()


# ==================== HELPERS ====================
async def get_or_create_wallet(user_id: int, db: AsyncSession) -> CommuterWallet:
    res = await db.execute(select(CommuterWallet).where(CommuterWallet.user_id == user_id))
    wallet = res.scalar_one_or_none()
    if not wallet:
        wallet = CommuterWallet(user_id=user_id, balance_zar=145.0)
        db.add(wallet)
        await db.commit()
        await db.refresh(wallet)
    return wallet


async def compute_summary_payload(db: AsyncSession) -> dict:
    tickets_res = await db.execute(select(BoardingTicket))
    tickets = tickets_res.scalars().all()
    db_revenue = sum(float(t.fare_zar) for t in tickets)
    db_ticket_count = len(tickets)

    inc_res = await db.execute(
        select(FleetIncident)
        .where(FleetIncident.is_active == True)  # noqa: E712
        .order_by(FleetIncident.created_at.desc())
    )
    incidents = inc_res.scalars().all()

    return {
        "total_fare_revenue": round(71040.0 + db_revenue, 2),
        "tickets_sold_today": 3840 + db_ticket_count,
        "incidents": [
            {
                "id": inc.id,
                "route_code": inc.route_code,
                "vehicle_plate": inc.vehicle_plate,
                "reporter_name": inc.reporter_name,
                "severity": inc.severity,
                "delay_minutes": inc.delay_minutes,
                "description": inc.description,
                "is_active": inc.is_active,
                "created_at": inc.created_at.isoformat(),
            }
            for inc in incidents
        ],
    }


# ==================== ROUTES ====================
@router.get("/wallet")
async def get_wallet_state(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wallet = await get_or_create_wallet(current_user.id, db)

    tx_res = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.user_id == current_user.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(15)
    )
    txs = tx_res.scalars().all()

    tix_res = await db.execute(
        select(BoardingTicket)
        .where(BoardingTicket.user_id == current_user.id)
        .order_by(BoardingTicket.issued_at.desc())
        .limit(10)
    )
    tickets = tix_res.scalars().all()

    return {
        "balance_zar": round(float(wallet.balance_zar), 2),
        "transactions": [
            {
                "id": t.id,
                "tx_type": t.tx_type,
                "title": t.title,
                "amount_zar": round(float(t.amount_zar), 2),
                "created_at": t.created_at.strftime("%H:%M"),
            }
            for t in txs
        ],
        "tickets": [
            {
                "ticket_code": tk.ticket_code,
                "route_code": tk.route_code,
                "route_name": tk.route_name,
                "fare_zar": round(float(tk.fare_zar), 2),
                "status": tk.status,
                "issued_at": tk.issued_at.strftime("%H:%M"),
            }
            for tk in tickets
        ],
    }


@router.post("/wallet/topup")
async def topup_wallet(
    payload: TopUpRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wallet = await get_or_create_wallet(current_user.id, db)
    wallet.balance_zar = round(float(wallet.balance_zar) + float(payload.amount_zar), 2)
    wallet.updated_at = datetime.now(timezone.utc)

    tx = WalletTransaction(
        user_id=current_user.id,
        tx_type="TOPUP",
        title="Wallet Top-Up (Instant ZAR)",
        amount_zar=float(payload.amount_zar),
    )
    db.add(tx)
    await db.commit()
    await db.refresh(wallet)

    return {"balance_zar": wallet.balance_zar, "tx_id": tx.id}


@router.post("/tickets/buy", status_code=status.HTTP_201_CREATED)
async def buy_ticket(
    payload: BuyTicketRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wallet = await get_or_create_wallet(current_user.id, db)
    if float(wallet.balance_zar) < float(payload.fare_zar):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient wallet balance. Please top up first.",
        )

    wallet.balance_zar = round(float(wallet.balance_zar) - float(payload.fare_zar), 2)
    wallet.updated_at = datetime.now(timezone.utc)

    short_uid = uuid.uuid4().hex[:6].upper()
    ticket_code = f"TP-{payload.route_code}-{short_uid}"

    ticket = BoardingTicket(
        ticket_code=ticket_code,
        user_id=current_user.id,
        route_code=payload.route_code,
        route_name=payload.route_name,
        fare_zar=float(payload.fare_zar),
        status="ACTIVE",
    )
    tx = WalletTransaction(
        user_id=current_user.id,
        tx_type="TICKET_PURCHASE",
        title=f"{payload.route_code} • {payload.route_name}",
        amount_zar=-float(payload.fare_zar),
    )
    db.add(ticket)
    db.add(tx)
    await db.commit()
    await db.refresh(ticket)
    await db.refresh(wallet)

    summary = await compute_summary_payload(db)
    await hub.broadcast_event({"type": "METRICS_UPDATED", **summary})

    return {
        "balance_zar": wallet.balance_zar,
        "ticket": {
            "ticket_code": ticket.ticket_code,
            "route_code": ticket.route_code,
            "route_name": ticket.route_name,
            "fare_zar": ticket.fare_zar,
            "status": ticket.status,
            "issued_at": ticket.issued_at.strftime("%H:%M"),
        },
    }


@router.post("/tickets/validate")
async def validate_ticket(
    payload: ValidateTicketRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(BoardingTicket).where(BoardingTicket.ticket_code == payload.ticket_code.strip())
    )
    ticket = res.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Boarding pass not found.")

    ticket.status = "VALIDATED"
    ticket.validated_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "ticket_code": ticket.ticket_code,
        "status": ticket.status,
        "validated_at": ticket.validated_at.strftime("%H:%M"),
    }


@router.get("/summary")
async def get_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await compute_summary_payload(db)


@router.post("/incidents", status_code=status.HTTP_201_CREATED)
async def create_incident(
    payload: CreateIncidentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in ("driver", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only drivers or management can broadcast route incidents.",
        )

    incident = FleetIncident(
        route_code=payload.route_code.upper(),
        vehicle_plate=payload.vehicle_plate.upper(),
        reporter_name=current_user.full_name,
        severity=payload.severity,
        delay_minutes=payload.delay_minutes,
        description=payload.description.strip(),
        is_active=True,
    )
    db.add(incident)
    await db.commit()
    await db.refresh(incident)

    summary = await compute_summary_payload(db)
    await hub.broadcast_event({"type": "INCIDENT_CREATED", **summary})

    return {"id": incident.id, "incidents": summary["incidents"]}


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(
    incident_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in ("driver", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized.")

    res = await db.execute(select(FleetIncident).where(FleetIncident.id == incident_id))
    inc = res.scalar_one_or_none()
    if not inc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found.")

    inc.is_active = False
    await db.commit()

    summary = await compute_summary_payload(db)
    await hub.broadcast_event({"type": "INCIDENT_RESOLVED", **summary})
    return {"incidents": summary["incidents"]}


@router.websocket("/ws/track/{trip_id}")
async def websocket_track(
    websocket: WebSocket,
    trip_id: int,
    db: AsyncSession = Depends(get_db),
):
    await hub.connect_trip(trip_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            lat = float(data["latitude"])
            lng = float(data["longitude"])
            speed = float(data.get("speed", 42.0))
            heading = float(data.get("heading", 120.0))
            occupancy = str(data.get("occupancy", "68%"))

            point_wkt = f"SRID=4326;POINT({lng} {lat})"
            insert_query = text(
                """
                INSERT INTO telemetry_logs (trip_id, geom, speed, heading)
                VALUES (:trip_id, ST_GeomFromEWKT(:wkt), :speed, :heading)
                """
            )
            await db.execute(
                insert_query,
                {"trip_id": trip_id, "wkt": point_wkt, "speed": speed, "heading": heading},
            )
            await db.commit()

            await hub.broadcast_trip(
                trip_id,
                {
                    "trip_id": trip_id,
                    "latitude": lat,
                    "longitude": lng,
                    "speed": speed,
                    "heading": heading,
                    "occupancy": occupancy,
                },
            )
    except WebSocketDisconnect:
        hub.disconnect_trip(trip_id, websocket)


@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await hub.connect_events(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect_events(websocket)