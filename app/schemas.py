from pydantic import BaseModel
from typing import List, Tuple, Optional
from datetime import datetime

class OperatorCreate(BaseModel):
    name: str
    contact_email: str
    fleet_size: int = 1

class OperatorResponse(OperatorCreate):
    operator_id: int
    class Config:
        from_attributes = True

class DriverCreate(BaseModel):
    operator_id: int
    full_name: str
    license_number: str
    phone_number: str

class DriverResponse(DriverCreate):
    driver_id: int
    class Config:
        from_attributes = True

class RouteCreate(BaseModel):
    operator_id: int
    route_name: str
    route_code: str
    coordinates: List[Tuple[float, float]]

class RouteResponse(BaseModel):
    route_id: int
    operator_id: int
    route_name: str
    route_code: str
    class Config:
        from_attributes = True

class TripStart(BaseModel):
    route_id: int
    driver_id: int
    vehicle_plate: str

class TripResponse(TripStart):
    trip_id: int
    status: str
    class Config:
        from_attributes = True

class NearbyBusResponse(BaseModel):
    trip_id: int
    vehicle_plate: str
    route_name: str
    route_code: str
    latitude: float
    longitude: float
    speed: float
    heading: float
    distance_meters: Optional[float] = None

    class Config:
        from_attributes = True

class OperatorAnalyticsResponse(BaseModel):
    operator_id: int
    operator_name: str
    total_drivers: int
    total_routes: int
    active_trips: int

    class Config:
        from_attributes = True

class TelemetryBreadcrumb(BaseModel):
    latitude: float
    longitude: float
    speed: float
    heading: float
    timestamp: datetime

    class Config:
        from_attributes = True