"""Data Pre-processing Engine (Module 2): validation, safe storage, EXIF/GPS/time extraction,
hashing (SHA-256 + perceptual hashes), thumbnails, video metadata and frame extraction."""
import hashlib
import io
import uuid
from pathlib import Path

import numpy as np
from PIL import ExifTags, Image, ImageOps

from .config import ALLOWED_IMAGE_TYPES, ALLOWED_VIDEO_TYPES, MAX_UPLOAD_MB, THUMB_DIR, UPLOAD_DIR

Image.MAX_IMAGE_PIXELS = 80_000_000  # guard against decompression bombs


class ValidationError(ValueError):
    pass


def sniff_mime(head: bytes):
    """Identify file type from magic bytes — the client-supplied content type is not trusted."""
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        return "video/quicktime" if brand == b"qt  " else "video/mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    return None


def validate_and_store(data: bytes, original_name: str):
    if not data:
        raise ValidationError("Empty file")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"File exceeds {MAX_UPLOAD_MB} MB limit")
    mime = sniff_mime(data[:16])
    if mime in ALLOWED_IMAGE_TYPES:
        kind, ext = "image", ALLOWED_IMAGE_TYPES[mime]
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
        except Exception as e:  # corrupted / malicious image
            raise ValidationError(f"Image could not be decoded: {e}")
    elif mime in ALLOWED_VIDEO_TYPES:
        kind, ext = "video", ALLOWED_VIDEO_TYPES[mime]
    else:
        raise ValidationError("Unsupported file type (allowed: JPEG, PNG, WEBP, MP4, MOV, WEBM)")
    # Safe server-generated filename; original name kept only as metadata.
    name = f"{uuid.uuid4().hex}{ext}"
    path = UPLOAD_DIR / name
    path.write_bytes(data)
    safe_original = Path(original_name or "upload").name[:120]
    return {"kind": kind, "mime": mime, "stored_path": str(path), "stored_name": name,
            "original_filename": safe_original, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


# ---------------------------------------------------------------- perceptual hashes
def _dct_matrix(n):
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    m[0] *= 1 / np.sqrt(2)
    return m * np.sqrt(2 / n)


_D32 = _dct_matrix(32)


def phash(img: Image.Image) -> str:
    g = np.asarray(img.convert("L").resize((32, 32), Image.LANCZOS), dtype=np.float64)
    d = _D32 @ g @ _D32.T
    low = d[:8, :8].flatten()
    med = np.median(low[1:])
    bits = low > med
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def dhash(img: Image.Image) -> str:
    g = np.asarray(img.convert("L").resize((9, 8), Image.LANCZOS), dtype=np.int32)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(h1, h2):
    if not h1 or not h2:
        return None
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def phash_variants(img: Image.Image):
    """pHashes of transformed copies (rotations, mirror, 80% centre crop) so re-posts that were rotated,
    flipped or cropped can still be matched against the stored original."""
    g = img.convert("L")
    w, h = g.size
    crop = g.crop((int(w * 0.1), int(h * 0.1), int(w * 0.9), int(h * 0.9)))
    return {"rot90": phash(g.rotate(90, expand=True)), "rot180": phash(g.rotate(180)), "rot270": phash(g.rotate(270, expand=True)),
            "mirror": phash(ImageOps.mirror(g)), "crop80": phash(crop)}


def content_hash(img: Image.Image) -> str:
    """SHA-256 of decoded, orientation-corrected pixels. Identical for copies whose file bytes differ only in
    metadata (e.g. EXIF stripped by a platform, or PNG re-saved losslessly)."""
    rgb = img.convert("RGB")
    return hashlib.sha256(rgb.tobytes() + f"{rgb.width}x{rgb.height}".encode()).hexdigest()


def validate_coords(lat, lon):
    """Return (ok, reason). Rejects out-of-range values and the (0,0) 'null island' placeholder."""
    if lat is None or lon is None:
        return False, "missing"
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False, "not numeric"
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False, "out of range"
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return False, "0,0 placeholder (common default when GPS has no fix)"
    return True, None


def in_india_extent(lat, lon):
    return 6.0 <= lat <= 38.0 and 68.0 <= lon <= 98.0


def mp4_creation_time(path: str):
    """Parse the ISO-BMFF/QuickTime 'moov/mvhd' creation_time (seconds since 1904-01-01 UTC). Returns ISO or None."""
    import struct
    from datetime import datetime, timedelta, timezone

    def boxes(f, start, end):
        pos = start
        while pos + 8 <= end:
            f.seek(pos)
            hdr = f.read(8)
            if len(hdr) < 8:
                return
            size, typ = struct.unpack(">I4s", hdr)
            hlen = 8
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
                hlen = 16
            elif size == 0:
                size = end - pos
            if size < hlen:
                return
            yield typ, pos + hlen, pos + size
            pos += size

    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            for typ, s, e in boxes(f, 0, end):
                if typ != b"moov":
                    continue
                for t2, s2, _ in boxes(f, s, e):
                    if t2 == b"mvhd":
                        f.seek(s2)
                        ver = f.read(1)[0]
                        f.read(3)
                        secs = struct.unpack(">Q", f.read(8))[0] if ver == 1 else struct.unpack(">I", f.read(4))[0]
                        if secs <= 0:
                            return None
                        dt = datetime(1904, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=secs)
                        if dt.year < 2000 or dt > datetime.now(timezone.utc) + timedelta(days=1):
                            return None  # unset / implausible clock
                        return dt.replace(microsecond=0).isoformat()
    except (OSError, struct.error, IndexError):
        return None
    return None


# ---------------------------------------------------------------- EXIF
def _ratio(v):
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            return v[0] / v[1]
        except Exception:
            return None


def _gps_to_deg(vals, ref):
    try:
        d, m, s = (_ratio(x) for x in vals)
        deg = d + m / 60 + s / 3600
        if ref in ("S", "W"):
            deg = -deg
        return round(deg, 6)
    except Exception:
        return None


def extract_exif(img: Image.Image):
    out = {"present": False, "tags": {}, "gps": None, "capture_time": None, "capture_offset": None,
           "modified_time": None, "software": None, "make": None, "model": None, "exif_dims": None}
    try:
        ex = img.getexif()
    except Exception:
        return out
    if not ex:
        return out
    out["present"] = True
    base = {ExifTags.TAGS.get(k, str(k)): v for k, v in ex.items()}
    sub = {}
    try:
        sub = {ExifTags.TAGS.get(k, str(k)): v for k, v in ex.get_ifd(0x8769).items()}
    except Exception:
        pass
    out["software"] = str(base.get("Software")).strip("\x00 ") if base.get("Software") else None
    out["make"] = str(base.get("Make")).strip("\x00 ") if base.get("Make") else None
    out["model"] = str(base.get("Model")).strip("\x00 ") if base.get("Model") else None
    out["modified_time"] = str(base.get("DateTime")) if base.get("DateTime") else None
    out["capture_time"] = str(sub.get("DateTimeOriginal") or sub.get("DateTimeDigitized") or "") or None
    out["capture_offset"] = str(sub.get("OffsetTimeOriginal")) if sub.get("OffsetTimeOriginal") else None
    if sub.get("ExifImageWidth") and sub.get("ExifImageHeight"):
        out["exif_dims"] = [int(sub["ExifImageWidth"]), int(sub["ExifImageHeight"])]
    out["gps_rejected"] = None
    try:
        gps_ifd = ex.get_ifd(0x8825)
        g = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
        if "GPSLatitude" in g and "GPSLongitude" in g:
            lat = _gps_to_deg(g["GPSLatitude"], g.get("GPSLatitudeRef", "N"))
            lon = _gps_to_deg(g["GPSLongitude"], g.get("GPSLongitudeRef", "E"))
            ok, why = validate_coords(lat, lon)
            if ok:
                gps = {"lat": lat, "lon": lon}
                if "GPSAltitude" in g:
                    gps["altitude_m"] = round(_ratio(g["GPSAltitude"]) or 0, 1)
                if "GPSDOP" in g:
                    gps["dop"] = round(_ratio(g["GPSDOP"]) or 0, 2)  # dilution of precision: lower is better
                if "GPSDateStamp" in g and "GPSTimeStamp" in g:
                    try:
                        hh, mm, ss = (_ratio(x) for x in g["GPSTimeStamp"])
                        gps["utc_time"] = f"{str(g['GPSDateStamp']).replace(':', '-')}T{int(hh):02d}:{int(mm):02d}:{int(ss):02d}+00:00"
                    except Exception:
                        pass
                out["gps"] = gps
            else:
                out["gps_rejected"] = {"lat": lat, "lon": lon, "reason": why}
    except Exception:
        pass
    keep = ("Make", "Model", "Software", "DateTime", "Orientation", "Artist")
    out["tags"] = {k: str(base[k])[:120] for k in keep if k in base}
    for k in ("DateTimeOriginal", "DateTimeDigitized", "OffsetTimeOriginal", "LensModel"):
        if k in sub:
            out["tags"][k] = str(sub[k])[:120]
    return out


def make_thumb(img: Image.Image, stem: str):
    t = ImageOps.exif_transpose(img.copy())
    t.thumbnail((480, 480))
    p = THUMB_DIR / f"{stem}.jpg"
    t.convert("RGB").save(p, "JPEG", quality=82)
    return str(p)


def process_image(path: str, stem: str):
    with Image.open(path) as img:
        img.load()
        exif = extract_exif(img)
        upright = ImageOps.exif_transpose(img)
        return {
            "width": img.width, "height": img.height, "format": img.format,
            "phash": phash(upright), "dhash": dhash(upright), "phash_variants": phash_variants(upright),
            "content_hash": content_hash(upright),
            "exif": exif, "thumb_path": make_thumb(img, stem), "frames": [],
        }


def process_video(path: str, stem: str):
    """Video metadata + representative frames (OpenCV). Container creation-time tags need ffprobe,
    which is not bundled; that limitation is reported rather than hidden."""
    try:
        import cv2
    except ImportError:
        return {"error": "OpenCV unavailable — video frames not extracted", "frames": [], "phash": None}
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"error": "Video could not be decoded", "frames": [], "phash": None}
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    ctime = mp4_creation_time(path) if path.lower().endswith((".mp4", ".mov")) else None
    meta = {"frame_count": n, "fps": round(fps, 2), "duration_s": round(n / fps, 2) if fps else None,
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "creation_time": ctime,
            "limitation": ("Container creation time read from the MP4/MOV 'mvhd' box; it is set by the recording app or "
                           "editor and is rewritten when a video is re-encoded or edited." if ctime else
                           "No usable container creation time (absent, zero, or not an MP4/MOV container). "
                           "Many apps reset it on export; absence is not evidence of fakery.")}
    frames, hashes = [], []
    for i, pos in enumerate([0.1, 0.5, 0.9] if n > 3 else [0]):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * pos))
        ok, frame = cap.read()
        if not ok:
            continue
        im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        fp = THUMB_DIR / f"{stem}_f{i}.jpg"
        im.save(fp, "JPEG", quality=85)
        frames.append(str(fp))
        hashes.append(phash(im))
    cap.release()
    thumb = None
    if frames:
        with Image.open(frames[len(frames) // 2]) as im:
            thumb = make_thumb(im, stem)
    return {"width": meta["width"], "height": meta["height"], "video_meta": meta, "frames": frames,
            "frame_phashes": hashes, "phash": hashes[len(hashes) // 2] if hashes else None, "dhash": None,
            "phash_variants": {}, "content_hash": None, "container_time": ctime,
            "exif": {"present": False}, "thumb_path": thumb}
