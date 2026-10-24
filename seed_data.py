import random
from database import SessionLocal, init_database, Depot, SmartBin, Vehicle

def seed():
    init_database()
    db = SessionLocal()

    if db.query(Depot).first():
        db.close()
        return

    # Khởi tạo Depot trung tâm
    depot = Depot(
        name="Trạm Điều Hành Đô Thị Trung Tâm",
        latitude=21.1860,
        longitude=106.0763,
        address="Trung tâm Điều hành Smart City"
    )
    db.add(depot)
    db.commit()
    db.refresh(depot)

    # Khởi tạo Đội xe: Xe chính (Truck 1) & Xe dự phòng (Truck 2)
    truck_primary = Vehicle(
        depot_id=depot.depot_id,
        license_plate="99C-888.88",
        capacity_tons=5.0,
        driver_name="Xe chính (Đội Cơ giới 01)",
        status="IDLE"
    )
    truck_backup = Vehicle(
        depot_id=depot.depot_id,
        license_plate="99C-999.99",
        capacity_tons=5.0,
        driver_name="Xe dự phòng (Đội Cứu trợ 02)",
        status="STANDBY"
    )
    db.add_all([truck_primary, truck_backup])

    # 12 thùng rác bố trí quanh khu vực đô thị
    random.seed(42)
    bins = []
    offsets = [
        (0.012, 0.008), (-0.010, 0.015), (0.018, -0.012), (-0.015, -0.018),
        (0.005, 0.022), (-0.022, 0.005), (0.025, 0.015), (-0.008, -0.025),
        (0.014, -0.020), (-0.018, 0.020), (0.002, -0.015), (-0.012, 0.002)
    ]
    for i, (d_lat, d_lon) in enumerate(offsets, start=1):
        sb = SmartBin(
            depot_id=depot.depot_id,
            bin_code=f"BIN-IOT-{i:02d}",
            latitude=round(depot.latitude + d_lat, 6),
            longitude=round(depot.longitude + d_lon, 6),
            height_cm=120.0,
            fill_threshold_pct=80.0,
            battery_capacity_mah=3200.0,
            status='ACTIVE'
        )
        bins.append(sb)

    db.add_all(bins)
    db.commit()

    # Tạo sẵn một vài bản ghi đo đạc ban đầu (Hot Start) để có ngay 3 thùng rác khẩn cấp demo
    # Thùng #02, #05, #09 đầy rác (khoảng cách 15-20cm ~ 85-88%)
    import datetime
    from database import TelemetryLog
    now = datetime.datetime.utcnow()
    initial_logs = []
    urgent_bin_ids = {2, 5, 9}
    for b in bins:
        if b.bin_id in urgent_bin_ids:
            dist = 18.0 # Rác đầy 85%
            pct = 85.0
            urg = True
        else:
            dist = 95.0 # Rác chỉ 20%
            pct = 20.8
            urg = False
        
        initial_logs.append(TelemetryLog(
            bin_id=b.bin_id,
            measured_distance_cm=dist,
            calculated_fill_pct=pct,
            battery_voltage_v=3.6,
            battery_remaining_mah=3180.0,
            is_urgent=urg,
            recorded_at=now
        ))
    db.add_all(initial_logs)
    db.commit()
    db.close()
    print("[INIT] Đã nạp thành công Depot, Xe gom, 12 Thùng rác và 3 điểm khẩn cấp ban đầu.")

if __name__ == "__main__":
    seed()