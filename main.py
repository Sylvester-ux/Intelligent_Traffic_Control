import cv2

from camera.webcam import Webcam
from detection.vehicle_detector import VehicleDetector
from tracking.vehicle_tracker import VehicleTracker
from analytics.vehicle_counter import Vehicle_counter
from analytics.congestion import CongestionAnalyzer
from analytics.road_region import RoadRegion


# Initializing system components
camera = Webcam()
detector = VehicleDetector()
tracker = VehicleTracker()
counter = Vehicle_counter()
congestion_analyzer = CongestionAnalyzer()
road_region = RoadRegion([
    (0.1, 0.3),
    (0.9, 0.3),
    (1.0, 0.95),
    (0.0, 0.95)
])

if not camera.is_opened():
    print("Error: Could not open camera")
    exit()


while True:

    success, frame = camera.read()

    if not success:
        print("Could not read frame")
        break

    # Object detection and tracking
    results = detector.detect(frame)

    # Movement analysis
    frame_objects = tracker.update(
        results,
        detector.model.names
    )

    # Identify objects within the road region
    road_objects = [
        obj for obj in frame_objects
        if road_region.contains(obj["center"], frame.shape)
    ]

    # Object counting
    counter.update(frame_objects)
    congestion_data = congestion_analyzer.update(road_objects)

    # Draw YOLO annotations
    annotated_frame = results[0].plot()
    road_region.draw(annotated_frame)

    # Draw movement information
    for obj in frame_objects:

        center_x, center_y = obj["center"]

        cv2.circle(
            annotated_frame,
            (center_x, center_y),
            4,
            (56, 201, 38),
            -1
        )

        cv2.putText(
            annotated_frame,
            f"ID {obj['track_id']}: {obj['movement']}",
            (center_x, center_y - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (222, 201, 38),
            2
        )

    # Draw virtual counting line
    cv2.line(
        annotated_frame,
        (0, counter.line_y),
        (annotated_frame.shape[1], counter.line_y),
        (187, 63, 19),
        2
    )

    # Display traffic statistics
    statistics = counter.get_statistics()

    cv2.putText(
        annotated_frame,
        f"Vehicles: {statistics['vehicles']}",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (22, 201, 38),
        2
    )

    cv2.putText(
        annotated_frame,
        f"Pedestrians: {statistics['pedestrians']}",
        (10, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (141, 11, 233),
        2
    )

    cv2.putText(
        annotated_frame,
        f"Other objects: {statistics['others']}",
        (10, 90),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2
    )

    cv2.putText(
        annotated_frame,
        f"Congestion: {congestion_data['congestion_level']}",
        (10, 120),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 255),
        2
    )

    cv2.imshow("Intelligence Traffic System", annotated_frame)

    # Exit when Q is pressed
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break


# Release resources
camera.release()
cv2.destroyAllWindows()