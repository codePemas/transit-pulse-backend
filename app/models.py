from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry
from app.database import Base
import datetime

class Operator(Base):
    __tablename__ = "operators"
    
    operator_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    contact_email = Column(String(100), nullable=False)
    fleet_size = Column(Integer, default=1)
    
    drivers = relationship("Driver", back_populates="operator", cascade="all, delete")
    routes = relationship("Route", back_populates="operator", cascade="all, delete")

class Driver(Base):
    __tablename__ = "drivers"
    
    driver_id = Column(Integer, primary_key=True, index=True)
    operator_id = Column(Integer, ForeignKey("operators.operator_id", ondelete="CASCADE"), nullable=False)
    full_name = Column(String(100), nullable=False)
    license_number = Column(String(50), unique=True, nullable=False)
    phone_number = Column(String(20), nullable=False)
    
    operator = relationship("Operator", back_populates="drivers")
    trips = relationship("Trip", back_populates="driver")

class Route(Base):
    __tablename__ = "routes"
    
    route_id = Column(Integer, primary_key=True, index=True)
    operator_id = Column(Integer, ForeignKey("operators.operator_id", ondelete="CASCADE"), nullable=False)
    route_name = Column(String(100), nullable=False)
    route_code = Column(String(20), unique=True, nullable=False)
    path_geometry = Column(Geometry(geometry_type="LINESTRING", srid=4326), nullable=False)
    
    operator = relationship("Operator", back_populates="routes")
    trips = relationship("Trip", back_populates="route")

class Trip(Base):
    __tablename__ = "trips"
    
    trip_id = Column(Integer, primary_key=True, index=True)
    route_id = Column(Integer, ForeignKey("routes.route_id", ondelete="CASCADE"), nullable=False)
    driver_id = Column(Integer, ForeignKey("drivers.driver_id", ondelete="CASCADE"), nullable=False)
    vehicle_plate = Column(String(20), nullable=False)
    status = Column(String(20), default="active")
    start_time = Column(DateTime, default=datetime.datetime.utcnow)
    
    route = relationship("Route", back_populates="trips")
    driver = relationship("Driver", back_populates="trips")
    telemetry = relationship("TripTelemetry", back_populates="trip", cascade="all, delete")

class TripTelemetry(Base):
    __tablename__ = "trip_telemetry"
    
    telemetry_id = Column(Integer, primary_key=True, index=True)
    trip_id = Column(Integer, ForeignKey("trips.trip_id", ondelete="CASCADE"), nullable=False)
    location = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    speed = Column(Float, default=0.0)
    heading = Column(Float, default=0.0)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    
    trip = relationship("Trip", back_populates="telemetry")