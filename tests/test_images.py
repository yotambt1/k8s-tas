from io import BytesIO

from PIL import Image

from taskplatform.models import TaskStatus
from taskplatform.worker import process_one


def image_bytes(image_format="PNG", size=(120, 60)):
    image = Image.new("RGB", size, (28, 120, 210))
    content = BytesIO()
    image.save(content, format=image_format)
    return content.getvalue()


def test_upload_resize_status_and_download(client, queue, store, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("OUTPUT_MAX_DIMENSION", "64")
    response = client.post(
        "/images",
        files={"file": ("photo.png", image_bytes(), "image/png")},
    )

    assert response.status_code == 202
    created = response.json()
    assert created["type"] == "resize"
    assert created["status"] == "queued"
    assert client.get(f"/tasks/{created['id']}").json()["status"] == "queued"

    assert process_one(queue, store) is True
    finished = client.get(f"/tasks/{created['id']}").json()
    assert finished["status"] == TaskStatus.SUCCEEDED
    assert finished["result"]["width"] == 64
    assert finished["result"]["height"] == 32

    download = client.get(f"/tasks/{created['id']}/result")
    assert download.status_code == 200
    assert download.headers["content-type"] == "image/png"
    with Image.open(BytesIO(download.content)) as output:
        assert output.size == (64, 32)
        assert output.format == "PNG"


def test_upload_rejects_corrupt_and_unsupported_images(client, queue):
    corrupt = client.post("/images", files={"file": ("bad.png", b"not an image", "image/png")})
    assert corrupt.status_code == 422

    gif = client.post(
        "/images",
        files={"file": ("animated.gif", image_bytes("GIF"), "image/gif")},
    )
    assert gif.status_code == 415
    assert queue.size() == 0


def test_upload_enforces_byte_and_pixel_limits(client, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "20")
    too_many_bytes = client.post(
        "/images", files={"file": ("large.png", image_bytes(), "image/png")}
    )
    assert too_many_bytes.status_code == 413
    assert queue.size() == 0

    monkeypatch.setenv("MAX_UPLOAD_BYTES", "10485760")
    monkeypatch.setenv("MAX_IMAGE_PIXELS", "100")
    too_many_pixels = client.post(
        "/images",
        files={"file": ("large.png", image_bytes(size=(11, 10)), "image/png")},
    )
    assert too_many_pixels.status_code == 413
    assert queue.size() == 0


def test_generic_tasks_cannot_inject_resize_paths(client, queue):
    response = client.post("/tasks", json={"type": "resize", "payload": {"input_key": "../../etc/passwd"}})
    assert response.status_code == 422
    assert queue.size() == 0
