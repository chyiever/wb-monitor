"""
Detection Results Storage Module

This module provides functionality to save detection results to files
in various formats (CSV, JSON) for analysis and record keeping.

Author: Claude
Date: 2026-03-11
"""

import os
import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

@dataclass
class StorableDetectionResult:
    """Detection result data structure for storage."""
    sequence_number: int
    timestamp: float
    datetime_str: str
    feature_name: str
    feature_value: float
    threshold: float
    baseline: float
    duration: Optional[float] = None
    status: str = "completed"  # "active", "completed", "timeout"

class DetectionStorage:
    """
    Storage manager for detection results.

    This class handles saving detection results to various file formats
    and provides functionality for data export and analysis.
    """

    def __init__(self, storage_path: str = "D:/PCCP/FIPmonitor"):
        """
        Initialize detection storage.

        Args:
            storage_path: Base directory for storing detection results
        """
        self.storage_path = Path(storage_path)
        self.logger = logging.getLogger(__name__ + '.DetectionStorage')

        # Ensure storage directory exists
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Create subdirectories for different data types
        self.daily_path = self.storage_path / "daily"
        self.summary_path = self.storage_path / "summary"
        self.export_path = self.storage_path / "export"

        for path in [self.daily_path, self.summary_path, self.export_path]:
            path.mkdir(parents=True, exist_ok=True)

        # Current session data
        self.session_detections = []
        self.session_start_time = datetime.now()

        # File handles for continuous writing
        self.current_csv_file = None
        self.current_csv_writer = None

        self.logger.info(f"DetectionStorage initialized: {self.storage_path}")

    def save_detection(self, detection_result) -> bool:
        """
        Save a single detection result.

        Args:
            detection_result: DetectionResult object from threshold_detector

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Convert to storable format
            storable = StorableDetectionResult(
                sequence_number=detection_result.sequence_number,
                timestamp=detection_result.timestamp,
                datetime_str=datetime.fromtimestamp(detection_result.timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                feature_name=detection_result.feature_name,
                feature_value=detection_result.feature_value,
                threshold=detection_result.threshold,
                baseline=detection_result.baseline,
                duration=detection_result.duration,
                status="completed" if detection_result.duration is not None else "active"
            )

            # Add to session data
            self.session_detections.append(storable)

            # Save to daily CSV file
            self._save_to_daily_csv(storable)

            # Save to JSON for detailed analysis (every 10 detections)
            if len(self.session_detections) % 10 == 0:
                self._save_session_json()

            return True

        except Exception as e:
            self.logger.error(f"Error saving detection result: {e}")
            return False

    def _save_to_daily_csv(self, detection: StorableDetectionResult):
        """Save detection to daily CSV file."""
        try:
            # Get today's date for filename
            date_str = datetime.now().strftime("%Y-%m-%d")
            csv_file = self.daily_path / f"detections_{date_str}.csv"

            # Check if file needs header
            needs_header = not csv_file.exists()

            # Open file and write detection
            with open(csv_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)

                # Write header if new file
                if needs_header:
                    writer.writerow([
                        '序号', '检测时间', '特征类型', '特征值',
                        '阈值', '基线值', '持续时长(s)', '状态'
                    ])

                # Write detection data
                writer.writerow([
                    detection.sequence_number,
                    detection.datetime_str,
                    self._get_feature_display_name(detection.feature_name),
                    f"{detection.feature_value:.6f}",
                    f"{detection.threshold:.6f}",
                    f"{detection.baseline:.6f}",
                    f"{detection.duration:.3f}" if detection.duration else "进行中",
                    detection.status
                ])

            self.logger.debug(f"Saved detection #{detection.sequence_number} to {csv_file}")

        except Exception as e:
            self.logger.error(f"Error saving to daily CSV: {e}")

    def _save_session_json(self):
        """Save current session detections to JSON file."""
        try:
            session_str = self.session_start_time.strftime("%Y%m%d_%H%M%S")
            json_file = self.summary_path / f"session_{session_str}.json"

            session_data = {
                "session_info": {
                    "start_time": self.session_start_time.isoformat(),
                    "detection_count": len(self.session_detections),
                    "duration_seconds": (datetime.now() - self.session_start_time).total_seconds()
                },
                "detections": [asdict(detection) for detection in self.session_detections]
            }

            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)

            self.logger.info(f"Saved session data to {json_file}")

        except Exception as e:
            self.logger.error(f"Error saving session JSON: {e}")

    def _get_feature_display_name(self, feature_name: str) -> str:
        """Convert feature name to Chinese display name."""
        display_names = {
            'short_energy': '短时能量',
            'zero_crossing': '过零率',
            'peak_factor': '峰值因子',
            'rms': 'RMS值'
        }
        return display_names.get(feature_name, feature_name)

    def export_daily_summary(self, date: Optional[datetime] = None) -> str:
        """
        Export daily detection summary.

        Args:
            date: Date to export (uses today if None)

        Returns:
            Path to exported summary file
        """
        try:
            if date is None:
                date = datetime.now()

            date_str = date.strftime("%Y-%m-%d")
            csv_file = self.daily_path / f"detections_{date_str}.csv"

            if not csv_file.exists():
                self.logger.warning(f"No detection data for {date_str}")
                return ""

            # Create summary
            summary_file = self.export_path / f"daily_summary_{date_str}.txt"

            with open(csv_file, 'r', encoding='utf-8') as f_in, \
                 open(summary_file, 'w', encoding='utf-8') as f_out:

                reader = csv.reader(f_in)
                next(reader)  # Skip header

                # Count detections by feature
                feature_counts = {}
                total_detections = 0

                for row in reader:
                    if len(row) >= 3:
                        feature_type = row[2]
                        feature_counts[feature_type] = feature_counts.get(feature_type, 0) + 1
                        total_detections += 1

                # Write summary
                f_out.write(f"PCCP断丝监测 - 日报告\n")
                f_out.write(f"日期: {date_str}\n")
                f_out.write(f"总检测次数: {total_detections}\n\n")

                f_out.write("按特征类型统计:\n")
                for feature, count in feature_counts.items():
                    f_out.write(f"  {feature}: {count}次\n")

                f_out.write(f"\n报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

            self.logger.info(f"Exported daily summary to {summary_file}")
            return str(summary_file)

        except Exception as e:
            self.logger.error(f"Error exporting daily summary: {e}")
            return ""

    def get_session_statistics(self) -> Dict[str, Any]:
        """
        Get current session statistics.

        Returns:
            Dictionary with session statistics
        """
        try:
            feature_counts = {}
            for detection in self.session_detections:
                feature_name = detection.feature_name
                feature_counts[feature_name] = feature_counts.get(feature_name, 0) + 1

            return {
                "total_detections": len(self.session_detections),
                "session_duration": (datetime.now() - self.session_start_time).total_seconds(),
                "feature_counts": feature_counts,
                "start_time": self.session_start_time.isoformat(),
                "storage_path": str(self.storage_path)
            }

        except Exception as e:
            self.logger.error(f"Error getting session statistics: {e}")
            return {}

    def cleanup(self):
        """Cleanup resources and finalize storage."""
        try:
            # Close any open file handles
            if self.current_csv_file:
                self.current_csv_file.close()
                self.current_csv_file = None

            # Save final session data
            if self.session_detections:
                self._save_session_json()

            # Export daily summary
            self.export_daily_summary()

            self.logger.info("DetectionStorage cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during storage cleanup: {e}")

    def clear_session_data(self):
        """Clear current session data."""
        self.session_detections.clear()
        self.session_start_time = datetime.now()
        self.logger.info("Session data cleared")