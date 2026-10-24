import datetime
import numpy as np
from fastapi import FastAPI, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import (
    SessionLocal, init_database, SmartBin, TelemetryLog,
    CollectionRoute, RouteWaypoint, Depot, Vehicle
)
from seed_data import seed

# Khởi tạo CSDL và nạp dữ liệu mẫu
init_database()
seed()

def ensure_fleet(db: Session):
    depot = db.query(Depot).first()
    if not depot:
        return
    vehicles = db.query(Vehicle).all()
    if len(vehicles) == 0:
        v1 = Vehicle(
            depot_id=depot.depot_id,
            license_plate="99C-888.88",
            capacity_tons=5.0,
            driver_name="Xe chính (Đội Cơ giới 01)",
            status="IDLE"
        )
        v2 = Vehicle(
            depot_id=depot.depot_id,
            license_plate="99C-999.99",
            capacity_tons=5.0,
            driver_name="Xe dự phòng (Đội Cứu trợ 02)",
            status="STANDBY"
        )
        db.add_all([v1, v2])
        db.commit()
    elif len(vehicles) == 1:
        v1 = vehicles[0]
        if "dự phòng" not in v1.driver_name.lower():
            v1.driver_name = "Xe chính (Đội Cơ giới 01)"
            v1.license_plate = "99C-888.88"
        v2 = Vehicle(
            depot_id=depot.depot_id,
            license_plate="99C-999.99",
            capacity_tons=5.0,
            driver_name="Xe dự phòng (Đội Cứu trợ 02)",
            status="STANDBY"
        )
        db.add(v2)
        db.commit()

with SessionLocal() as init_db:
    ensure_fleet(init_db)

import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

app = FastAPI(title="Smart Waste CPS - Digital Twin Platform")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==============================================================================
# 1. DIGITAL TWIN: BỘ LỌC ƯỚC LƯỢNG TRẠNG THÁI (1D KALMAN FILTER)
# ==============================================================================
class DigitalTwinStateEstimator:
    """Khử nhiễu đo khoảng cách siêu âm trước khi xác định ngưỡng cảnh báo"""

    def __init__(self):
        self.estimates = {}  # {bin_id: estimated_height_cm}
        self.error_cov = {}  # {bin_id: P}
        self.process_noise_q = 0.5  # Q: Độ biến động tự nhiên của mức rác
        self.sensor_noise_r = 2.0  # R: Phương sai nhiễu cảm biến siêu âm

    def update(self, bin_id: int, raw_height: float) -> float:
        if bin_id not in self.estimates:
            self.estimates[bin_id] = raw_height
            self.error_cov[bin_id] = 1.0
            return raw_height

        # Time Update (Predict)
        x_pred = self.estimates[bin_id]
        p_pred = self.error_cov[bin_id] + self.process_noise_q

        # Measurement Update (Kalman Gain)
        k_gain = p_pred / (p_pred + self.sensor_noise_r)
        x_est = x_pred + k_gain * (raw_height - x_pred)
        p_est = (1.0 - k_gain) * p_pred

        self.estimates[bin_id] = x_est
        self.error_cov[bin_id] = p_est
        return x_est

    def reset_bin(self, bin_id: int):
        """Đặt lại trạng thái số của Digital Twin khi thùng được dọn sạch"""
        self.estimates[bin_id] = 0.0
        self.error_cov[bin_id] = 1.0


dt_filter = DigitalTwinStateEstimator()


# ==============================================================================
# 2. SCHEMAS
# ==============================================================================
class TelemetryPayload(BaseModel):
    bin_id: int
    measured_distance_cm: float
    battery_voltage_v: float
    battery_remaining_mah: float


class ActuationPayload(BaseModel):
    bin_id: int


# ==============================================================================
# 3. CONTROLLERS & REST APIS
# ==============================================================================
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # Cú pháp chuẩn của Starlette/FastAPI phiên bản mới
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/gateway/telemetry")
def receive_telemetry(payload: TelemetryPayload, db: Session = Depends(get_db)):
    """Cổng giao tiếp Gateway -> Backend Server (Ingestion Layer)"""
    bin_obj = db.query(SmartBin).filter(SmartBin.bin_id == payload.bin_id).first()
    if not bin_obj:
        return JSONResponse(status_code=404, content={"error": "Bin not found"})

    # Đoạn tính toán chiều cao rác thô
    raw_waste_height = max(0.0, bin_obj.height_cm - payload.measured_distance_cm)

    # Lọc nhiễu qua Digital Twin Kalman Filter
    filtered_height = dt_filter.update(payload.bin_id, raw_waste_height)
    fill_pct = min(100.0, (filtered_height / bin_obj.height_cm) * 100.0)
    is_urgent = fill_pct >= bin_obj.fill_threshold_pct

    log = TelemetryLog(
        bin_id=payload.bin_id,
        measured_distance_cm=round(payload.measured_distance_cm, 2),
        calculated_fill_pct=round(fill_pct, 2),
        battery_voltage_v=round(payload.battery_voltage_v, 3),
        battery_remaining_mah=round(payload.battery_remaining_mah, 2),
        is_urgent=is_urgent,
        recorded_at=datetime.datetime.utcnow()
    )
    db.add(log)
    db.commit()
    return {
        "status": "SUCCESS",
        "fill_level": log.calculated_fill_pct,
        "filtered_height_cm": round(filtered_height, 2),
        "is_urgent": is_urgent
    }


@app.post("/api/actuator/empty")
def empty_bin_actuator(payload: ActuationPayload, db: Session = Depends(get_db)):
    """Cơ cấu Actuation: Xả rỗng đơn lẻ một thùng rác"""
    bin_obj = db.query(SmartBin).filter(SmartBin.bin_id == payload.bin_id).first()
    if not bin_obj:
        return JSONResponse(status_code=404, content={"error": "Bin not found"})

    # Reset trạng thái Digital Twin
    dt_filter.reset_bin(payload.bin_id)

    log = TelemetryLog(
        bin_id=payload.bin_id,
        measured_distance_cm=bin_obj.height_cm,
        calculated_fill_pct=0.0,
        battery_voltage_v=3.6,
        battery_remaining_mah=3150.0,
        is_urgent=False,
        recorded_at=datetime.datetime.utcnow()
    )
    db.add(log)
    db.commit()
    return {"status": "SUCCESS", "message": f"Thùng {bin_obj.bin_code} đã được dọn sạch về 0%"}


@app.post("/api/fleet/auto-dispatch")
def auto_dispatch_fleet(db: Session = Depends(get_db)):
    """Kích hoạt xe gom rác tự động giải phóng toàn bộ các thùng khẩn cấp (có hỗ trợ xe dự phòng)"""
    ensure_fleet(db)
    bins = db.query(SmartBin).all()
    urgent_bins = []

    for b in bins:
        latest = (
            db.query(TelemetryLog)
            .filter(TelemetryLog.bin_id == b.bin_id)
            .order_by(TelemetryLog.recorded_at.desc())
            .first()
        )
        if latest and latest.is_urgent:
            urgent_bins.append(b)

    if not urgent_bins:
        return {"status": "NO_ACTION", "message": "Không có điểm rác nào cần thu gom."}

    vehicles = db.query(Vehicle).all()
    primary_truck = next((v for v in vehicles if "888" in v.license_plate or "chính" in v.driver_name.lower()), vehicles[0] if vehicles else None)
    backup_truck = next((v for v in vehicles if "999" in v.license_plate or "dự phòng" in v.driver_name.lower()), vehicles[1] if len(vehicles) > 1 else None)

    # Cơ chế Failover: Chọn xe khả dụng
    if primary_truck and primary_truck.status not in ["BREAKDOWN", "MAINTENANCE", "OFF_DUTY"]:
        dispatched_truck = primary_truck
        is_backup = False
    elif backup_truck:
        dispatched_truck = backup_truck
        is_backup = True
    else:
        dispatched_truck = primary_truck
        is_backup = False

    if dispatched_truck:
        dispatched_truck.status = "EN_ROUTE"
        db.commit()

    # Reset toàn bộ các thùng đầy về 0%
    now = datetime.datetime.utcnow()
    for b in urgent_bins:
        dt_filter.reset_bin(b.bin_id)
        log = TelemetryLog(
            bin_id=b.bin_id,
            measured_distance_cm=b.height_cm,
            calculated_fill_pct=0.0,
            battery_voltage_v=3.6,
            battery_remaining_mah=3150.0,
            is_urgent=False,
            recorded_at=now
        )
        db.add(log)

    if dispatched_truck:
        dispatched_truck.status = "IDLE" if not is_backup else "STANDBY"
    db.commit()

    truck_label = f"Xe dự phòng ({dispatched_truck.license_plate})" if is_backup else f"Xe chính ({dispatched_truck.license_plate})"
    return {
        "status": "SUCCESS",
        "cleared_bins_count": len(urgent_bins),
        "dispatched_vehicle": truck_label,
        "is_backup": is_backup,
        "message": f"{truck_label} đã hoàn thành thu gom {len(urgent_bins)} thùng rác khẩn cấp!"
    }


@app.get("/api/dashboard/state")
def get_dashboard_state(db: Session = Depends(get_db)):
    ensure_fleet(db)
    depot = db.query(Depot).first()
    bins = db.query(SmartBin).all()

    bin_data = []
    urgent_bins = []
    for b in bins:
        latest_log = (
            db.query(TelemetryLog)
            .filter(TelemetryLog.bin_id == b.bin_id)
            .order_by(TelemetryLog.recorded_at.desc())
            .first()
        )
        fill = latest_log.calculated_fill_pct if latest_log else 10.0
        bat_v = latest_log.battery_voltage_v if latest_log else 3.6
        bat_mah = latest_log.battery_remaining_mah if latest_log else 3200.0
        urgent = latest_log.is_urgent if latest_log else False

        # Ước tính khối lượng rác: Thùng 120cm chứa tối đa ~ 0.45 tấn rác
        waste_weight_tons = round((fill / 100.0) * 0.45, 2)

        item = {
            "bin_id": b.bin_id,
            "bin_code": b.bin_code,
            "lat": b.latitude,
            "lng": b.longitude,
            "fill_pct": fill,
            "waste_weight_tons": waste_weight_tons,
            "battery_v": bat_v,
            "battery_mah": bat_mah,
            "is_urgent": urgent
        }
        bin_data.append(item)
        if urgent:
            urgent_bins.append(item)

    route_result = compute_route(depot, urgent_bins)

    vehicles = db.query(Vehicle).all()
    primary_truck = next((v for v in vehicles if "888" in v.license_plate or "chính" in v.driver_name.lower()), vehicles[0] if vehicles else None)
    backup_truck = next((v for v in vehicles if "999" in v.license_plate or "dự phòng" in v.driver_name.lower()), vehicles[1] if len(vehicles) > 1 else None)

    # Đánh giá cơ chế chuyển mạch dự phòng (Failover)
    failover_active = False
    if primary_truck and primary_truck.status in ["BREAKDOWN", "MAINTENANCE"]:
        active_truck = backup_truck if backup_truck else primary_truck
        failover_active = True
    else:
        active_truck = primary_truck if primary_truck else (backup_truck if backup_truck else None)

    fleet_info = [
        {
            "vehicle_id": v.vehicle_id,
            "license_plate": v.license_plate,
            "driver_name": v.driver_name,
            "capacity_tons": v.capacity_tons,
            "status": v.status,
            "is_primary": (v == primary_truck),
            "is_active": (v == active_truck)
        }
        for v in vehicles
    ]

    vehicle_info = {
        "vehicle_id": active_truck.vehicle_id if active_truck else 1,
        "license_plate": active_truck.license_plate if active_truck else "99C-888.88",
        "capacity_tons": active_truck.capacity_tons if active_truck else 5.0,
        "driver_name": active_truck.driver_name if active_truck else "Xe chính (Đội Cơ giới 01)",
        "status": active_truck.status if active_truck else "IDLE",
        "is_backup": failover_active
    }

    # Lấy số liệu thống kê lũy kế từ bảng collection_routes
    completed_routes = db.query(CollectionRoute).filter(CollectionRoute.status == 'COMPLETED').all()
    total_trips = len(completed_routes)
    cum_cps_dist = sum(r.total_distance_km for r in completed_routes)
    # Lộ trình tĩnh truyền thống: luôn đi qua đủ 12 thùng ~ 28.5 km mỗi chuyến
    cum_static_dist = total_trips * 28.5
    saved_dist = max(0.0, cum_static_dist - cum_cps_dist)
    # Định mức tiêu hao xe gom rác: ~ 0.28 L diesel/km, 1 L diesel sinh ra 2.68 kg CO2
    saved_fuel_liters = round(saved_dist * 0.28, 1)
    saved_co2_kg = round(saved_fuel_liters * 2.68, 1)

    cumulative_stats = {
        "total_trips": total_trips,
        "total_cps_dist_km": round(cum_cps_dist, 2),
        "saved_dist_km": round(saved_dist, 2),
        "saved_fuel_liters": saved_fuel_liters,
        "saved_co2_kg": saved_co2_kg
    }

    return {
        "depot": {"name": depot.name, "lat": depot.latitude, "lng": depot.longitude},
        "bins": bin_data,
        "vehicle": vehicle_info,
        "fleet": fleet_info,
        "failover_active": failover_active,
        "urgent_count": len(urgent_bins),
        "route": route_result,
        "cumulative_stats": cumulative_stats
    }


class CompleteRoutePayload(BaseModel):
    total_distance_km: float
    cleared_bins_count: int
    vehicle_id: int | None = None


@app.post("/api/fleet/complete-route")
def complete_route_record(payload: CompleteRoutePayload, db: Session = Depends(get_db)):
    """Ghi nhận chuyến thu gom đã hoàn tất vào CSDL để tổng hợp số liệu lũy kế"""
    vehicle_id = payload.vehicle_id
    if not vehicle_id:
        vehicle = db.query(Vehicle).first()
        vehicle_id = vehicle.vehicle_id if vehicle else 1

    # Ước tính nhiên liệu tiêu thụ cho chuyến này (0.28 L/km)
    fuel_est = round(payload.total_distance_km * 0.28, 2)

    route_record = CollectionRoute(
        vehicle_id=vehicle_id,
        created_at=datetime.datetime.utcnow(),
        total_distance_km=payload.total_distance_km,
        fuel_consumed_est_l=fuel_est,
        status='COMPLETED'
    )
    db.add(route_record)
    db.commit()

    return {"status": "SUCCESS", "message": "Đã lưu lịch sử chuyến đi thành công!"}


@app.post("/api/fleet/breakdown-toggle")
def toggle_primary_vehicle_breakdown(db: Session = Depends(get_db)):
    """Kích hoạt / Khắc phục sự cố giả lập hỏng máy trên Xe chính (Failover test)"""
    ensure_fleet(db)
    vehicles = db.query(Vehicle).all()
    primary_truck = next((v for v in vehicles if "888" in v.license_plate or "chính" in v.driver_name.lower()), vehicles[0] if vehicles else None)
    backup_truck = next((v for v in vehicles if "999" in v.license_plate or "dự phòng" in v.driver_name.lower()), vehicles[1] if len(vehicles) > 1 else None)

    if not primary_truck:
        return JSONResponse(status_code=404, content={"error": "Không tìm thấy xe chính"})

    if primary_truck.status == "BREAKDOWN":
        # Khắc phục sự cố: phục hồi xe chính
        primary_truck.status = "IDLE"
        if backup_truck and backup_truck.status != "EN_ROUTE":
            backup_truck.status = "STANDBY"
        db.commit()
        return {
            "status": "SUCCESS",
            "breakdown_active": False,
            "message": f"✅ Xe chính ({primary_truck.license_plate}) đã được sửa chữa xong, quay lại trạng thái IDLE!",
            "active_vehicle": primary_truck.license_plate
        }
    else:
        # Kích hoạt hỏng hóc: chuyển giao failover sang xe dự phòng
        primary_truck.status = "BREAKDOWN"
        if backup_truck and backup_truck.status == "STANDBY":
            backup_truck.status = "IDLE"
        db.commit()
        return {
            "status": "SUCCESS",
            "breakdown_active": True,
            "message": f"🚨 SỰ CỐ: Xe chính ({primary_truck.license_plate}) bị hỏng hóc! Hệ thống Cyber tự động Failover kích hoạt Xe dự phòng ({backup_truck.license_plate if backup_truck else 'Xe 2'}) tiếp quản!",
            "active_vehicle": backup_truck.license_plate if backup_truck else "Xe 2"
        }


# ==============================================================================
# QUẢN LÝ ÙN TẮC GIAO THÔNG (CYBER TRAFFIC CONGESTION SERVICE)
# ==============================================================================
congested_zone = {
    "active": False,
    "name": "Nút giao Đô thị Đông Bắc (Ùn tắc cao điểm)",
    "lat": 21.196,
    "lng": 106.088,
    "radius_km": 1.5,
    "cost_multiplier": 3.2  # Tắc đường làm tăng chi phí thời gian lên 3.2 lần
}


@app.post("/api/traffic/toggle")
def toggle_traffic_jam():
    """Bật/Tắt sự cố ùn tắc giao thông vật lý để kiểm chứng tái định tuyến CPS"""
    congested_zone["active"] = not congested_zone["active"]
    return {
        "status": "SUCCESS",
        "traffic_jam_active": congested_zone["active"],
        "zone": congested_zone
    }


# ==============================================================================
# 4. THUẬT TOÁN ĐỊNH TUYẾN TỐI ƯU 2-OPT TSP (TÍCH HỢP HỆ SỐ GIAO THÔNG)
# ==============================================================================
def compute_route(depot, urgent_bins):
    if not urgent_bins:
        return {"waypoints": [], "total_distance_km": 0.0, "fuel_saved_pct": 0.0, "traffic_jam": congested_zone}

    points = [(depot.latitude, depot.longitude)] + [(b["lat"], b["lng"]) for b in urgent_bins]
    n = len(points)
    dist_matrix = np.zeros((n, n))
    actual_geo_matrix = np.zeros((n, n))

    for i in range(n):
        for j in range(n):
            if i != j:
                dlat = (points[i][0] - points[j][0]) * 111.0
                dlon = (points[i][1] - points[j][1]) * 111.0 * np.cos(np.radians(points[i][0]))
                geo_dist = np.hypot(dlat, dlon)
                actual_geo_matrix[i][j] = geo_dist

                # Kiểm tra xem đoạn đường có đi qua vùng ùn tắc giao thông không
                edge_cost = geo_dist
                if congested_zone["active"]:
                    mid_lat = (points[i][0] + points[j][0]) / 2.0
                    mid_lng = (points[i][1] + points[j][1]) / 2.0
                    dist_to_jam = np.hypot(
                        (mid_lat - congested_zone["lat"]) * 111.0,
                        (mid_lng - congested_zone["lng"]) * 111.0
                    )
                    if dist_to_jam <= congested_zone["radius_km"]:
                        # Phạt nặng chi phí các cung đường qua điểm kẹt xe để thuật toán tự động tránh
                        edge_cost *= congested_zone["cost_multiplier"]

                dist_matrix[i][j] = edge_cost

    unvisited = set(range(1, n))
    current = 0
    tour = [current]
    while unvisited:
        next_n = min(unvisited, key=lambda idx: dist_matrix[current][idx])
        tour.append(next_n)
        unvisited.remove(next_n)
        current = next_n

    def tour_length(r):
        return sum(dist_matrix[r[k]][r[k + 1]] for k in range(len(r) - 1)) + dist_matrix[r[-1]][r[0]]

    improved = True
    best_dist = tour_length(tour)
    while improved:
        improved = False
        for i in range(1, len(tour) - 1):
            for k in range(i + 1, len(tour)):
                new_tour = tour[:i] + tour[i:k + 1][::-1] + tour[k + 1:]
                new_dist = tour_length(new_tour)
                if new_dist < best_dist - 1e-4:
                    tour = new_tour
                    best_dist = new_dist
                    improved = True
                    break
            if improved:
                break

    full_path = tour + [0]
    waypoints = []
    for idx in full_path:
        # Nếu idx > 0 thì tương ứng với urgent_bins[idx - 1]
        bin_id_val = urgent_bins[idx - 1]["bin_id"] if idx > 0 else 0
        waypoints.append({
            "lat": points[idx][0],
            "lng": points[idx][1],
            "bin_id": bin_id_val
        })

    actual_total_km = sum(
        actual_geo_matrix[full_path[k]][full_path[k + 1]]
        for k in range(len(full_path) - 1)
    )

    return {
        "waypoints": waypoints,
        "total_distance_km": round(actual_total_km, 2),
        "fuel_saved_pct": round(max(0.0, (1.0 - len(urgent_bins) / 12.0) * 100.0), 1),
        "traffic_jam": congested_zone
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True, app_dir=BASE_DIR)