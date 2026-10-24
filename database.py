import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String,
    Float, Boolean, DateTime, ForeignKey, Enum
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

class Depot(Base):
    __tablename__ = "depots"

    depot_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    address = Column(String(255), nullable=True)

    bins = relationship("SmartBin", back_populates="depot")
    vehicles = relationship("Vehicle", back_populates="depot")

class SmartBin(Base):
    __tablename__ = "smart_bins"

    bin_id = Column(Integer, primary_key=True, autoincrement=True)
    depot_id = Column(Integer, ForeignKey("depots.depot_id"), nullable=False)
    bin_code = Column(String(30), unique=True, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    height_cm = Column(Float, default=120.0, nullable=False)
    fill_threshold_pct = Column(Float, default=80.0, nullable=False)
    battery_capacity_mah = Column(Float, default=3200.0, nullable=False)
    status = Column(Enum('ACTIVE', 'MAINTENANCE', 'OFFLINE', name="bin_status_enum"), default='ACTIVE')

    depot = relationship("Depot", back_populates="bins")
    telemetry_logs = relationship("TelemetryLog", back_populates="bin", cascade="all, delete-orphan")
    waypoints = relationship("RouteWaypoint", back_populates="bin")

class TelemetryLog(Base):
    __tablename__ = "telemetry_logs"

    telemetry_id = Column(Integer, primary_key=True, autoincrement=True)
    bin_id = Column(Integer, ForeignKey("smart_bins.bin_id"), nullable=False)
    recorded_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    measured_distance_cm = Column(Float, nullable=False)
    calculated_fill_pct = Column(Float, nullable=False)
    battery_voltage_v = Column(Float, nullable=False)
    battery_remaining_mah = Column(Float, nullable=False)
    is_urgent = Column(Boolean, default=False, nullable=False)

    bin = relationship("SmartBin", back_populates="telemetry_logs")

class Vehicle(Base):
    __tablename__ = "vehicles"

    vehicle_id = Column(Integer, primary_key=True, autoincrement=True)
    depot_id = Column(Integer, ForeignKey("depots.depot_id"), nullable=False)
    license_plate = Column(String(20), unique=True, nullable=False)
    capacity_tons = Column(Float, default=5.0, nullable=False)
    driver_name = Column(String(100), nullable=False)
    status = Column(Enum('IDLE', 'EN_ROUTE', 'UNLOADING', 'OFF_DUTY', name="vehicle_status_enum"), default='IDLE')

    depot = relationship("Depot", back_populates="vehicles")
    routes = relationship("CollectionRoute", back_populates="vehicle")

class CollectionRoute(Base):
    __tablename__ = "collection_routes"

    route_id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.vehicle_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    total_distance_km = Column(Float, default=0.0, nullable=False)
    fuel_consumed_est_l = Column(Float, default=0.0, nullable=False)
    status = Column(Enum('PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', name="route_status_enum"), default='PLANNED')

    vehicle = relationship("Vehicle", back_populates="routes")
    waypoints = relationship("RouteWaypoint", back_populates="route", cascade="all, delete-orphan", order_by="RouteWaypoint.sequence_order")

class RouteWaypoint(Base):
    __tablename__ = "route_waypoints"

    waypoint_id = Column(Integer, primary_key=True, autoincrement=True)
    route_id = Column(Integer, ForeignKey("collection_routes.route_id"), nullable=False)
    bin_id = Column(Integer, ForeignKey("smart_bins.bin_id"), nullable=False)
    sequence_order = Column(Integer, nullable=False)
    visited_at = Column(DateTime, nullable=True)
    action_status = Column(Enum('PENDING', 'EMPTY_COMPLETED', 'SKIPPED', name="waypoint_action_enum"), default='PENDING')

    route = relationship("CollectionRoute", back_populates="waypoints")
    bin = relationship("SmartBin", back_populates="waypoints")

DB_FILE = "smart_waste_cps.db"
engine = create_engine(f"sqlite:///{DB_FILE}?check_same_thread=False", echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_database():
    Base.metadata.create_all(bind=engine)