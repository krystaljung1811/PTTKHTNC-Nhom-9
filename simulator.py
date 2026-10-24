import time
import random
import requests

API_GATEWAY_URL = "http://127.0.0.1:8000/api/gateway/telemetry"
API_STATE_URL = "http://127.0.0.1:8000/api/dashboard/state"


def run_cps_simulator():
    print("=" * 70)
    print("  CPS PHYSICAL SIMULATOR: MÔ PHỎNG ĐỘNG LỰC HỌC TÍCH LŨY RÁC & PIN")
    print("=" * 70)

    # Khởi tạo không gian vật lý cho 12 thùng rác
    # 120.0cm = Rỗng; 10.0cm = Đầy kịch nắp (Ngưỡng 80% là <= 24cm)
    # Khởi tạo một số thùng ở mức 30-50cm để nhanh chóng đạt ngưỡng 80% (trong vòng 15-25 giây)
    bin_physical_state = {}
    for i in range(1, 13):
        init_dist = random.uniform(35.0, 65.0) if i in [2, 5, 9, 11] else random.uniform(75.0, 105.0)
        bin_physical_state[i] = {
            "current_distance": init_dist,
            "battery_mah": 3200.0,
            "accum_rate": random.uniform(3.5, 7.5)  # Rác dâng 3.5 - 7.5cm mỗi lần đổ
        }

    step = 0
    while True:
        step += 1

        # Đồng bộ ngược từ Cyber Digital Twin về Physical Space
        if step % 4 == 0:
            try:
                res = requests.get(API_STATE_URL, timeout=1.5).json()
                for b in res.get("bins", []):
                    # Nếu server báo đã xả dọn rác (mức đầy <= 1%), reset trạng thái vật lý về 120cm
                    if b["fill_pct"] <= 1.0 and bin_physical_state[b["bin_id"]]["current_distance"] < 100.0:
                        bin_physical_state[b["bin_id"]]["current_distance"] = 120.0
                        print(f"🔄 [PHYSICAL SYNC] Thùng #{b['bin_id']:02d} đã được xả rỗng ngoài thực tế!")
            except Exception:
                pass

        # 1-3 thùng ngẫu nhiên tiếp nhận thêm rác từ người dân
        active_bins = random.sample(range(1, 13), k=random.randint(1, 3))
        for bin_id in active_bins:
            state = bin_physical_state[bin_id]

            # Động lực học nạp rác: Giới hạn nhỏ nhất là 10.0cm
            state["current_distance"] = max(10.0, state["current_distance"] - state["accum_rate"])

            # Mô phỏng nhiễu cảm biến siêu âm (Gaussian Noise N(0, 1.2))
            sensor_noise = random.gauss(0, 1.2)
            measured_dist = max(10.0, state["current_distance"] + sensor_noise)

            # Mô hình xả pin Li-SOCl2
            state["battery_mah"] = max(200.0, state["battery_mah"] - 0.20)
            voltage = round(3.60 if state["battery_mah"] > 600 else 3.25 + (state["battery_mah"] / 600) * 0.35, 3)

            payload = {
                "bin_id": bin_id,
                "measured_distance_cm": round(measured_dist, 1),
                "battery_voltage_v": voltage,
                "battery_remaining_mah": round(state["battery_mah"], 2)
            }

            try:
                resp = requests.post(API_GATEWAY_URL, json=payload, timeout=2.0)
                if resp.status_code == 200:
                    data = resp.json()
                    status = "🚨 BÁO ĐỘNG ĐẦY" if data.get("is_urgent") else "🟢 Bình thường"
                    print(
                        f"[IOT-TX] Bin #{bin_id:02d} | Đo: {measured_dist:5.1f}cm | Đầy (sau Kalman): {data.get('fill_level'):5.1f}% | Pin: {voltage}V -> {status}")
            except Exception as e:
                print(f"[MẤT KẾT NỐI GATEWAY] {e}")

        time.sleep(2.0)


if __name__ == "__main__":
    run_cps_simulator()