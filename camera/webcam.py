import cv2
from ultralytics import YOLO
from detection.object_classifier import classify_object

model = YOLO('yolo11n.pt')

camera = cv2.VideoCapture(0)

if not camera.isOpened():
    print("Error: Could not open camera")
    exit()

previous_centers = {}
movement_threshold = 5

while True:
    success, frame = camera.read()

    if not success:
        print("Could not read frame")
        break

    frame = cv2.flip(frame, 1)

    results = model.track(
        frame,
        conf=0.3,
        verbose=False,
        persist=True,
        tracker="bytetrack.yaml"
    )

    boxes = results[0].boxes
    frame_objects = []

    if boxes is not None and boxes.id is not None:

        track_ids = boxes.id.int().cpu().tolist()
        class_ids = boxes.cls.int().cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        coordinates = boxes.xyxy.cpu().tolist()

        for track_id, class_id, confidence, coordinate in zip(
            track_ids, class_ids, confidences, coordinates
        ):

            object_name = model.names[class_id]
            category = classify_object(class_id)

            # Calculate object centre
            center_x = int((coordinate[0] + coordinate[2]) / 2)
            center_y = int((coordinate[1] + coordinate[3]) / 2)

            current_center = (center_x, center_y)

            # Calculate movement
            if track_id in previous_centers:

                old_center = previous_centers[track_id]

                displacement = (
                    (center_x - old_center[0]) ** 2 +
                    (center_y - old_center[1]) ** 2
                ) ** 0.5

                movement_status = (
                    "Moving" if displacement > movement_threshold
                    else "Stationary"
                )

            else:
                displacement = 0
                movement_status = "Initializing"

            previous_centers[track_id] = current_center

            # Organize object information
            object_data = {
                "track_id": track_id,
                "class_id": class_id,
                "object_name": object_name,
                "category": category,
                "confidence": round(confidence, 3),
                "coordinates": coordinate,
                "center": current_center,
                "displacement": round(displacement, 2),
                "movement": movement_status
            }

            frame_objects.append(object_data)

    # Draw YOLO annotations
    annotated_frame = results[0].plot()

    # Draw movement information
    for obj in frame_objects:

        center_x, center_y = obj["center"]

        cv2.circle(
            annotated_frame,
            (center_x, center_y),
            5,
            (0, 255, 0),
            -1
        )

        cv2.putText(
            annotated_frame,
            f'ID {obj["track_id"]}: {obj["movement"]}',
            (center_x, center_y - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            2
        )

    cv2.imshow("Traffic Intelligence", annotated_frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

camera.release()
cv2.destroyAllWindows()