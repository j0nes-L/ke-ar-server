import os
import gzip
import json
import struct
import re
from io import BytesIO
from pathlib import Path
from typing import AsyncIterator, Optional
import asyncio

import numpy as np
from PIL import Image
from .database import delete_bin_file_entry


class ExtractionProgress:
    def __init__(self, total_frames: int):
        self.total_frames = total_frames
        self.current_frame = 0
        self.color_extracted = 0
        self.depth_extracted = 0
        self.errors: list[str] = []
        self.status = "pending"
    
    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "total_frames": self.total_frames,
            "current_frame": self.current_frame,
            "color_extracted": self.color_extracted,
            "depth_extracted": self.depth_extracted,
            "progress_percent": round((self.current_frame / self.total_frames * 100) if self.total_frames > 0 else 0, 1),
            "errors": self.errors
        }


extraction_progress: dict[str, ExtractionProgress] = {}


def decompress_gzip(compressed_data: bytes) -> Optional[bytes]:
    if not compressed_data or len(compressed_data) < 4:
        return None
    gzip_data = compressed_data[4:]
    try:
        return gzip.decompress(gzip_data)
    except Exception:
        return None


def process_color_frame(raw_color: bytes, width: int, height: int) -> Optional[Image.Image]:
    try:
        img_color = Image.frombytes("RGBA", (width, height), raw_color)
        img_color = img_color.transpose(Image.FLIP_TOP_BOTTOM)
        return img_color
    except Exception:
        return None


def process_jpeg_frame(jpeg_data: bytes) -> Optional[Image.Image]:
    try:
        img = Image.open(BytesIO(jpeg_data))
        return img.convert("RGB")
    except Exception:
        return None


def process_depth_frame(raw_depth: bytes, d_width: int, d_height: int) -> Optional[Image.Image]:
    try:
        depth_floats = np.frombuffer(raw_depth, dtype=np.float32)
        total_pixels = len(depth_floats)
        expected_pixels = d_height * d_width
        
        if total_pixels != expected_pixels:
            if total_pixels % d_width == 0:
                d_height_actual = total_pixels // d_width
                depth_array = depth_floats.reshape((d_height_actual, d_width))
                if d_height_actual == d_width * 2:
                    depth_array = depth_array[d_width:, :]
            elif total_pixels % d_height == 0:
                d_width_actual = total_pixels // d_height
                depth_array = depth_floats.reshape((d_height, d_width_actual))
                if d_width_actual == d_height * 2:
                    depth_array = depth_array[:, d_height:]
            else:
                side = int(np.sqrt(total_pixels))
                if side * side == total_pixels:
                    depth_array = depth_floats.reshape((side, side))
                else:
                    return None
        else:
            depth_array = depth_floats.reshape((d_height, d_width))

        depth_array = np.nan_to_num(depth_array, nan=0.0, posinf=0.0, neginf=0.0)
        valid_mask = depth_array > 0
        
        if np.any(valid_mask):
            min_depth = np.percentile(depth_array[valid_mask], 2)
            max_depth = np.percentile(depth_array[valid_mask], 98)
            depth_clipped = np.clip(depth_array, min_depth, max_depth)
            depth_range = max_depth - min_depth
            if depth_range > 0.001:
                depth_normalized = (1.0 - (depth_clipped - min_depth) / depth_range) * 255.0
            else:
                depth_normalized = np.full_like(depth_array, 128.0)
        else:
            depth_normalized = np.zeros_like(depth_array)
        
        depth_uint8 = depth_normalized.astype(np.uint8)
        img_depth = Image.fromarray(depth_uint8, mode="L")
        img_depth = img_depth.transpose(Image.FLIP_TOP_BOTTOM)
        return img_depth
    except Exception:
        return None


async def extract_images(session_id: str, files_dir: Path) -> AsyncIterator[dict]:
    session_dir = files_dir / session_id
    visual_json_path = session_dir / "visual_data.json"
    
    progress = ExtractionProgress(0)
    extraction_progress[session_id] = progress
    progress.status = "initializing"
    yield progress.to_dict()
    
    if not visual_json_path.exists():
        progress.status = "error"
        progress.errors.append("visual_data.json not found")
        yield progress.to_dict()
        return
    
    try:
        with open(visual_json_path, 'r', encoding='utf-8-sig') as f:
            visual_data = json.load(f)
    except Exception as e:
        progress.status = "error"
        progress.errors.append(f"Failed to parse visual_data.json: {str(e)}")
        yield progress.to_dict()
        return
    
    bin_filename = visual_data.get("binaryFile")
    if not bin_filename:
        progress.status = "error"
        progress.errors.append("No binaryFile specified in visual_data.json")
        yield progress.to_dict()
        return
    
    bin_path = session_dir / bin_filename
    if not bin_path.exists():
        progress.status = "error"
        progress.errors.append(f"Binary file {bin_filename} not found")
        yield progress.to_dict()
        return
    
    info = visual_data.get("info", {})
    c_width = info.get("imageWidth", 1280)
    c_height = info.get("imageHeight", 960)
    d_width = info.get("depthWidth", 320)
    d_height = info.get("depthHeight", 320)
    color_format = info.get("colorFormat", "RGBA")
    depth_format = info.get("depthFormat", "Float32_GZip")
    
    frames = visual_data.get("frames", [])
    progress.total_frames = len(frames)
    
    if not frames:
        progress.status = "error"
        progress.errors.append("No frames found in visual_data.json")
        yield progress.to_dict()
        return
    
    color_dir = session_dir / "color_images"
    depth_dir = session_dir / "depth_images"
    color_dir.mkdir(parents=True, exist_ok=True)
    depth_dir.mkdir(parents=True, exist_ok=True)
    
    progress.status = "extracting"
    yield progress.to_dict()
    
    with open(bin_path, 'rb') as f_bin:
        for idx, frame in enumerate(frames):
            f_idx = frame.get("frameIndex", idx)
            
            if frame.get("colorSize", 0) > 0:
                try:
                    f_bin.seek(frame["colorOffset"])
                    color_data = f_bin.read(frame["colorSize"])
                    img_color = None
                    if color_format == "JPEG":
                        img_color = process_jpeg_frame(color_data)
                    else:
                        raw_color = decompress_gzip(color_data)
                        if raw_color:
                            img_color = process_color_frame(raw_color, c_width, c_height)
                    if img_color:
                        color_out_path = color_dir / f"frame_{f_idx:04d}.png"
                        img_color.save(str(color_out_path), format="PNG")
                        progress.color_extracted += 1
                except Exception as e:
                    progress.errors.append(f"Frame {f_idx} color error: {str(e)}")

            if frame.get("depthSize", 0) > 0:
                try:
                    f_bin.seek(frame["depthOffset"])
                    depth_data = f_bin.read(frame["depthSize"])
                    raw_depth = None
                    if depth_format == "Float32_Raw":
                        raw_depth = depth_data
                    else:
                        raw_depth = decompress_gzip(depth_data)
                    if raw_depth:
                        img_depth = process_depth_frame(raw_depth, d_width, d_height)
                        if img_depth:
                            depth_out_path = depth_dir / f"frame_{f_idx:04d}.png"
                            img_depth.save(str(depth_out_path), format="PNG")
                            progress.depth_extracted += 1
                except Exception as e:
                    progress.errors.append(f"Frame {f_idx} depth error: {str(e)}")
            
            progress.current_frame = idx + 1
            if (idx + 1) % 5 == 0 or (idx + 1) == len(frames):
                yield progress.to_dict()
            await asyncio.sleep(0)
    
    if bin_path.exists():
        try:
            os.remove(bin_path)
            await delete_bin_file_entry(session_id, bin_filename)
        except Exception as e:
            progress.errors.append(f"Failed to delete bin file: {str(e)}")

    progress.status = "completed"
    yield progress.to_dict()


def get_extraction_progress(session_id: str) -> Optional[dict]:
    if session_id in extraction_progress:
        return extraction_progress[session_id].to_dict()
    return None


def check_images_available(session_id: str, files_dir: Path, limit: int = 0, offset: int = 0) -> dict:
    session_dir = files_dir / session_id
    color_dir = session_dir / "color_images"
    depth_dir = session_dir / "depth_images"
    
    color_images = []
    depth_images = []
    
    if color_dir.exists():
        color_images = sorted([f.name for f in color_dir.glob("*.png")])
    if depth_dir.exists():
        depth_images = sorted([f.name for f in depth_dir.glob("*.png")])
    
    total_color = len(color_images)
    total_depth = len(depth_images)
    
    if limit > 0:
        color_images = color_images[offset:offset + limit]
        depth_images = depth_images[offset:offset + limit]
    
    return {
        "session_id": session_id,
        "color_available": total_color > 0,
        "depth_available": total_depth > 0,
        "color_count": total_color,
        "depth_count": total_depth,
        "color_images": color_images,
        "depth_images": depth_images,
        "offset": offset,
        "limit": limit
    }


def check_bin_file_exists(session_id: str, files_dir: Path) -> dict:
    session_dir = files_dir / session_id
    visual_json_path = session_dir / "visual_data.json"
    
    result = {
        "session_id": session_id,
        "visual_json_exists": visual_json_path.exists(),
        "bin_file_exists": False,
        "bin_filename": None,
        "frame_count": 0
    }
    
    if not visual_json_path.exists():
        return result
    
    try:
        with open(visual_json_path, 'r', encoding='utf-8-sig') as f:
            visual_data = json.load(f)
        bin_filename = visual_data.get("binaryFile")
        result["bin_filename"] = bin_filename
        result["frame_count"] = len(visual_data.get("frames", []))
        if bin_filename:
            bin_path = session_dir / bin_filename
            result["bin_file_exists"] = bin_path.exists()
    except Exception:
        pass
    
    return result


def load_tracking_data(session_id: str, files_dir: Path) -> Optional[dict]:
    tracking_path = files_dir / session_id / "tracking_data.json"
    if not tracking_path.exists():
        return None
    try:
        with open(tracking_path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return None


def load_visual_data(session_id: str, files_dir: Path) -> Optional[dict]:
    visual_path = files_dir / session_id / "visual_data.json"
    if not visual_path.exists():
        return None
    try:
        with open(visual_path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return None


def find_closest_tracking_frame(visual_timestamp_ms: int, tracking_frames: list, tolerance_ms: int = 100) -> Optional[dict]:
    if not tracking_frames or visual_timestamp_ms is None:
        return None
    
    closest = None
    min_diff = float('inf')
    
    for tf in tracking_frames:
        tf_ts = tf.get("timestampMs")
        if tf_ts is None:
            continue
        diff = abs(tf_ts - visual_timestamp_ms)
        if diff < min_diff:
            min_diff = diff
            closest = tf
    
    if closest and min_diff <= tolerance_ms:
        return closest
    return None


def get_frame_metadata(session_id: str, frame_index: int, files_dir: Path) -> Optional[dict]:
    visual_data = load_visual_data(session_id, files_dir)
    tracking_data = load_tracking_data(session_id, files_dir)
    
    result = {
        "frame_index": frame_index,
        "visual": None,
        "tracking": None
    }
    
    visual_frame = None
    if visual_data:
        frames = visual_data.get("frames", [])
        for frame in frames:
            if frame.get("frameIndex") == frame_index:
                visual_frame = frame
                result["visual"] = {
                    "timestamp": frame.get("timestamp"),
                    "timestampMs": frame.get("timestampMs"),
                    "pose": frame.get("pose"),
                    "distanceAtCenter": frame.get("distanceAtCenter"),
                    "hasColor": frame.get("colorSize", 0) > 0,
                    "hasDepth": frame.get("depthSize", 0) > 0
                }
                break
    
    if tracking_data and visual_frame:
        visual_ts = visual_frame.get("timestampMs")
        tracking_frames = tracking_data.get("frames", [])
        closest_tracking = find_closest_tracking_frame(visual_ts, tracking_frames)
        
        if closest_tracking:
            result["tracking"] = {
                "trackingFrameIndex": closest_tracking.get("frameIndex"),
                "timestamp": closest_tracking.get("timestamp"),
                "timestampMs": closest_tracking.get("timestampMs"),
                "timeDiffMs": abs(closest_tracking.get("timestampMs", 0) - visual_ts) if visual_ts else None,
                "leftHand": closest_tracking.get("leftHand"),
                "rightHand": closest_tracking.get("rightHand"),
                "leftEye": closest_tracking.get("leftEye"),
                "rightEye": closest_tracking.get("rightEye")
            }
    
    return result


def get_frames_metadata_paginated(session_id: str, files_dir: Path, limit: int = 20, offset: int = 0) -> dict:
    visual_data = load_visual_data(session_id, files_dir)
    tracking_data = load_tracking_data(session_id, files_dir)
    
    visual_frames = visual_data.get("frames", []) if visual_data else []
    tracking_frames = tracking_data.get("frames", []) if tracking_data else []
    
    total = len(visual_frames)
    paginated_frames = visual_frames[offset:offset + limit]
    
    frames = []
    for vframe in paginated_frames:
        fidx = vframe.get("frameIndex")
        visual_ts = vframe.get("timestampMs")
        
        closest_tracking = find_closest_tracking_frame(visual_ts, tracking_frames)
        
        frame_data = {
            "frame_index": fidx,
            "timestamp": vframe.get("timestamp"),
            "timestampMs": visual_ts,
            "pose": vframe.get("pose"),
            "distanceAtCenter": vframe.get("distanceAtCenter"),
            "hasColor": vframe.get("colorSize", 0) > 0,
            "hasDepth": vframe.get("depthSize", 0) > 0,
            "hasTracking": closest_tracking is not None,
            "leftHandTracked": False,
            "rightHandTracked": False
        }
        
        if closest_tracking:
            left_hand = closest_tracking.get("leftHand", {})
            right_hand = closest_tracking.get("rightHand", {})
            frame_data["leftHandTracked"] = left_hand.get("isTracked", False) if left_hand else False
            frame_data["rightHandTracked"] = right_hand.get("isTracked", False) if right_hand else False
        
        frames.append(frame_data)
    
    return {
        "session_id": session_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "frames": frames
    }


def get_session_metadata(session_id: str, files_dir: Path) -> dict:
    visual_data = load_visual_data(session_id, files_dir)
    tracking_data = load_tracking_data(session_id, files_dir)
    
    if not visual_data:
        return None
    
    visual_frames = visual_data.get("frames", [])
    tracking_frames = tracking_data.get("frames", []) if tracking_data else []
    
    result = {
        "sessionId": visual_data.get("sessionId", session_id),
        "headsetType": visual_data.get("headsetType", "Unknown"),
        "startTime": visual_data.get("startTime", ""),
        "cameraAccessSupported": visual_data.get("cameraAccessSupported", False),
        "depthSupported": visual_data.get("depthSupported", False),
        "raycastSupported": visual_data.get("raycastSupported", False),
        "binaryFile": visual_data.get("binaryFile"),
        "visualInfo": visual_data.get("info"),
        "trackingInfo": tracking_data.get("info") if tracking_data else None,
        "totalVisualFrames": len(visual_frames),
        "totalTrackingFrames": len(tracking_frames)
    }
    
    return result
