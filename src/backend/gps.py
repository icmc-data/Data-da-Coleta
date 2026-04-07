import logging
from PIL.ExifTags import GPSTAGS
import piexif

logger = logging.getLogger(__name__)


def _get_gps_info(image) -> dict | None:
    """
    Try piexif first (works well with JPEG & pillow-heif), then fallback to Pillow Exif.
    Returns a named dict of GPS tags or None.
    """
    exif_bytes = image.info.get("exif")
    if exif_bytes:
        try:
            exif_dict = piexif.load(exif_bytes)
            gps = exif_dict.get("GPS") or {}
            if gps:
                return {GPSTAGS.get(tag, tag): val for tag, val in gps.items()} or None
        except Exception as e:
            logger.warning(f"[GPS] piexif parse failed: {e}")

    try:
        exif = image.getexif()
        if not exif:
            return None
        gps_ifd = exif.get(0x8825)
        if not gps_ifd:
            return None
        return {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()} or None
    except Exception as e:
        logger.warning(f"[GPS] Pillow getexif fallback failed: {e}")
        return None


def _to_float(rat) -> float:
    if isinstance(rat, tuple):
        try:
            return float(rat[0]) / float(rat[1])
        except ZeroDivisionError:
            return 0.0
    return float(rat)


def _dms_to_dd(dms, ref) -> float:
    dd = _to_float(dms[0]) + _to_float(dms[1]) / 60.0 + _to_float(dms[2]) / 3600.0
    ref = ref.decode() if isinstance(ref, (bytes, bytearray)) else ref
    return -dd if ref in ('S', 'W') else dd


def extract_coordinates(image) -> tuple[float | None, float | None]:
    """Returns (latitude, longitude) in decimal degrees, or (None, None) if unavailable."""
    gps_info = _get_gps_info(image)
    if not gps_info:
        logger.info("[GPS] No GPS IFD present in image.")
        return None, None

    lat_dms = gps_info.get("GPSLatitude")
    lat_ref = gps_info.get("GPSLatitudeRef")
    lon_dms = gps_info.get("GPSLongitude")
    lon_ref = gps_info.get("GPSLongitudeRef")

    if lat_dms and lat_ref and lon_dms and lon_ref:
        return _dms_to_dd(lat_dms, lat_ref), _dms_to_dd(lon_dms, lon_ref)

    logger.info("[GPS] GPS IFD present but latitude/longitude fields are incomplete.")
    return None, None
