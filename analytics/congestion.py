from collections import deque
import time


class CongestionAnalyzer:

    def __init__(
        self,
        window_seconds=10,
        moderate_threshold=3,
        high_threshold=8,
        stationary_threshold=0.5,
        moderate_stationary_ratio=0.3
    ):

        self.window_seconds = window_seconds

        self.moderate_threshold = moderate_threshold
        self.high_threshold = high_threshold
        self.stationary_threshold = stationary_threshold
        self.moderate_stationary_ratio = moderate_stationary_ratio

        self.observations = deque()

    def update(self, frame_objects):

        current_time = time.monotonic()

        # Extract vehicles currently detected
        vehicles = [
            obj for obj in frame_objects
            if obj["category"] == "vehicle"
        ]

        vehicle_count = len(vehicles)

        # Determine the proportion of stationary vehicles
        stationary_count = sum(
            1 for obj in vehicles
            if obj["movement"] == "Stationary"
        )

        stationary_ratio = (
            stationary_count / vehicle_count
            if vehicle_count > 0 else 0
        )

        # Store current observation
        self.observations.append({
            "time": current_time,
            "vehicle_count": vehicle_count,
            "stationary_ratio": stationary_ratio
        })

        # Remove observations outside the time window
        while (
            self.observations and
            current_time - self.observations[0]["time"]
            > self.window_seconds
        ):
            self.observations.popleft()

        # Calculate average traffic measurements
        average_vehicle_count = sum(
            obs["vehicle_count"] for obs in self.observations
        ) / len(self.observations)

        average_stationary_ratio = sum(
            obs["stationary_ratio"] for obs in self.observations
        ) / len(self.observations)

        # Estimate congestion level
        if (
            average_vehicle_count >= self.high_threshold
            and average_stationary_ratio >= self.stationary_threshold
        ):
            congestion_level = "High"

        elif (
            average_vehicle_count >= self.moderate_threshold
            and average_stationary_ratio >= self.moderate_stationary_ratio
        ):
            congestion_level = "Moderate"

        else:
            congestion_level = "Low"

        return {
            "vehicle_count": vehicle_count,
            "stationary_count": stationary_count,
            "stationary_ratio": round(average_stationary_ratio, 2),
            "average_vehicle_count": round(average_vehicle_count, 2),
            "congestion_level": congestion_level
        }