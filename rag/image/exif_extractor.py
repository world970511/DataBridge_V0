"""
EXIF 메타데이터 추출 모듈.

Pillow를 사용하여 이미지 파일에서 EXIF 데이터를 추출합니다.
카메라 정보, GPS 좌표, 촬영 일시, 이미지 크기 등을 구조화된 딕셔너리로 반환합니다.
EXIF가 없거나 손상된 파일도 안전하게 처리합니다.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ExifTags

logger = logging.getLogger(__name__)


@dataclass
class ImageExifData:
    """이미지에서 추출된 EXIF 메타데이터."""
    width: int = 0
    height: int = 0
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    lens_info: Optional[str] = None
    focal_length: Optional[float] = None
    aperture: Optional[float] = None
    shutter_speed: Optional[str] = None
    iso: Optional[int] = None
    date_taken: Optional[datetime] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    gps_altitude: Optional[float] = None
    orientation: Optional[int] = None
    exif_raw: dict = field(default_factory=dict)


def extract_exif(file_path: str) -> ImageExifData:
    """
    이미지 파일에서 EXIF 메타데이터를 추출.

    Args:
        file_path: 이미지 파일 절대 경로.

    Returns:
        ImageExifData 인스턴스. EXIF가 없으면 width/height만 채워진 객체.
    """
    result = ImageExifData()

    try:
        with Image.open(file_path) as img:
            result.width, result.height = img.size

            exif_dict = _get_exif_dict(img)
            if not exif_dict:
                return result

            result.exif_raw = {
                k: str(v) for k, v in exif_dict.items()
                if isinstance(v, (str, int, float, bool))
            }

            result.camera_make = exif_dict.get("Make")
            result.camera_model = exif_dict.get("Model")
            result.lens_info = (
                exif_dict.get("LensModel")
                or exif_dict.get("LensInfo")
                or exif_dict.get("Lens")
            )
            if isinstance(result.lens_info, tuple):
                result.lens_info = str(result.lens_info)

            focal = exif_dict.get("FocalLength")
            if focal is not None:
                result.focal_length = float(focal) if not isinstance(focal, tuple) else float(focal[0]) / float(focal[1])

            fnumber = exif_dict.get("FNumber")
            if fnumber is not None:
                result.aperture = float(fnumber) if not isinstance(fnumber, tuple) else float(fnumber[0]) / float(fnumber[1])

            exposure = exif_dict.get("ExposureTime")
            if exposure is not None:
                if isinstance(exposure, tuple):
                    result.shutter_speed = f"{exposure[0]}/{exposure[1]}"
                else:
                    result.shutter_speed = f"1/{int(1/float(exposure))}" if float(exposure) < 1 else str(exposure)

            iso_val = exif_dict.get("ISOSpeedRatings") or exif_dict.get("PhotographicSensitivity")
            if iso_val is not None:
                result.iso = int(iso_val) if not isinstance(iso_val, tuple) else int(iso_val[0])

            result.date_taken = _parse_exif_datetime(
                exif_dict.get("DateTimeOriginal") or exif_dict.get("DateTime")
            )

            result.orientation = exif_dict.get("Orientation")

            gps_info = exif_dict.get("GPSInfo")
            if gps_info and isinstance(gps_info, dict):
                lat, lon, alt = _parse_gps_info(gps_info)
                result.gps_latitude = lat
                result.gps_longitude = lon
                result.gps_altitude = alt

    except Exception as e:
        logger.warning(f"EXIF extraction failed for {Path(file_path).name}: {e}")

    return result


def _get_exif_dict(image: Image.Image) -> dict:
    """PIL Image에서 EXIF 태그를 {태그명: 값} 딕셔너리로 변환."""
    try:
        exif_data = image.getexif()
        if not exif_data:
            return {}

        result = {}
        for tag_id, value in exif_data.items():
            tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
            result[tag_name] = value

        # IFD EXIF (상세 촬영 정보)
        ifd_exif = exif_data.get_ifd(ExifTags.IFD.Exif)
        if ifd_exif:
            for tag_id, value in ifd_exif.items():
                tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                result[tag_name] = value

        # IFD GPSInfo
        ifd_gps = exif_data.get_ifd(ExifTags.IFD.GPSInfo)
        if ifd_gps:
            gps_dict = {}
            for tag_id, value in ifd_gps.items():
                gps_tag = ExifTags.GPSTAGS.get(tag_id, str(tag_id))
                gps_dict[gps_tag] = value
            result["GPSInfo"] = gps_dict

        return result

    except Exception as e:
        logger.debug(f"Failed to parse EXIF: {e}")
        return {}


def _parse_gps_info(gps_info: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """EXIF GPSInfo를 (latitude, longitude, altitude) 플로트 튜플로 변환."""
    lat = lon = alt = None

    try:
        lat_data = gps_info.get("GPSLatitude")
        lat_ref = gps_info.get("GPSLatitudeRef", "N")
        if lat_data:
            lat = _dms_to_decimal(lat_data)
            if lat_ref == "S":
                lat = -lat

        lon_data = gps_info.get("GPSLongitude")
        lon_ref = gps_info.get("GPSLongitudeRef", "E")
        if lon_data:
            lon = _dms_to_decimal(lon_data)
            if lon_ref == "W":
                lon = -lon

        alt_data = gps_info.get("GPSAltitude")
        alt_ref = gps_info.get("GPSAltitudeRef", 0)
        if alt_data is not None:
            alt = float(alt_data)
            if alt_ref == 1:
                alt = -alt

    except Exception as e:
        logger.debug(f"GPS parsing failed: {e}")

    return lat, lon, alt


def _dms_to_decimal(dms) -> float:
    """DMS (degrees, minutes, seconds) 튜플을 십진도로 변환."""
    degrees = float(dms[0])
    minutes = float(dms[1])
    seconds = float(dms[2])
    return degrees + minutes / 60.0 + seconds / 3600.0


def _parse_exif_datetime(dt_str) -> Optional[datetime]:
    """EXIF 날짜 문자열 'YYYY:MM:DD HH:MM:SS'를 datetime으로 변환."""
    if not dt_str or not isinstance(dt_str, str):
        return None
    try:
        return datetime.strptime(dt_str.strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(dt_str.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            logger.debug(f"Cannot parse EXIF date: {dt_str}")
            return None
