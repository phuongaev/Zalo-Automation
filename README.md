# Zalo Post Trigger API

Tool tối giản để **n8n gọi theo lịch**.

Flow mới chỉ còn:
1. n8n gọi `POST /trigger`
2. tool gọi API lấy content: `https://go.dungmoda.com/webhook/zalo-mkt-dang-bai-len-tuong`
3. bật các LDPlayer trong config
4. đăng cùng 1 nội dung cho các account
5. chạy theo batch `3 máy / lượt`
6. xong batch thì tắt máy ảo
7. trả JSON kết quả về cho n8n

## Phase 1
Hiện tại chỉ giữ 1 máy để test:
- `account_id: zalo02`
- `adb_serial: 127.0.0.1:5559`
- `emulator_index: 2`

Khi flow ổn, chỉ cần thêm account vào `config/accounts.yaml`.

## Cài đặt
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -U pip
pip install -e .
```

## Chạy server
```powershell
python run.py
```

Mặc định server chạy ở:
- `http://127.0.0.1:8787`

## API
### Health
```powershell
Invoke-RestMethod http://127.0.0.1:8787/health
```

### Trigger run tất cả account enabled
```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8787/trigger -ContentType 'application/json' -Body '{}'
```

### Trigger riêng 1 account
```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8787/trigger -ContentType 'application/json' -Body '{"account_ids":["zalo02"]}'
```

### Debug capture
```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8787/api/debug/capture/zalo02
```

### Xem file debug từ xa
- Liệt kê file debug:
```powershell
Invoke-RestMethod http://127.0.0.1:8787/api/debug/files/zalo02
```
- Mở screenshot trực tiếp:
```text
http://127.0.0.1:8787/api/debug/file/zalo02/screen.png
```
- Mở XML trực tiếp:
```text
http://127.0.0.1:8787/api/debug/file/zalo02/window_dump.adb.xml
http://127.0.0.1:8787/api/debug/file/zalo02/window_dump.u2.xml
```
- Xem log mới nhất:
```text
http://127.0.0.1:8787/api/logs
```

## Config
File: `config/accounts.yaml`

```yaml
global:
  dry_run: true
  batch_size: 3
  stop_emulator_after_run: true
  adb_path: D:\LDPlayer\LDPlayer3.0\adb.exe
  ldconsole_path: D:\LDPlayer\LDPlayer3.0\ldconsole.exe
  content_api:
    url: https://go.dungmoda.com/webhook/zalo-mkt-dang-bai-len-tuong
    method: GET
    headers: {}
    payload: {}
accounts:
  - account_id: zalo02
    enabled: true
    emulator_name: leidian2
    emulator_index: 2
    adb_serial: 127.0.0.1:5559
    login:
      phone: ""
      password: ""
```

## Ghi chú thực tế
- `uiautomator2` trên LDPlayer 3.0 có thể lỗi, nên tool có endpoint debug để lấy screenshot/XML nếu được
- Chưa giả vờ là automation đã ổn tuyệt đối; selector vẫn cần calibrate theo app Zalo thực tế
- Nên test `dry_run: true` trước, sau đó mới chuyển `dry_run: false`

## Output từ /trigger
Tool trả JSON gồm:
- post lấy từ content API
- batch_size
- số account chạy
- số thành công/thất bại
- kết quả chi tiết từng account
