"""JPEG encoding for recorded camera frames."""


def encode_jpeg(image, quality: int) -> bytes:
    import cv2  # optional dependency: pip install .[camera]
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return encoded.tobytes()
