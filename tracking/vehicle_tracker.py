from detection.object_classifier import classify_object


class VehicleTracker:

    def __init__(self, movement_threshold=5):
        self.previous_centers = {}
        self.movement_threshold = movement_threshold

    def update(self, results, class_names):

        boxes = results[0].boxes
        frame_objects = []

        if boxes is None or boxes.id is None:
            return frame_objects

        track_ids = boxes.id.int().cpu().tolist()
        class_ids = boxes.cls.int().cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        coordinates = boxes.xyxy.cpu().tolist()

        for track_id, class_id, confidence, coordinate in zip(
            track_ids, class_ids, confidences, coordinates
        ):

            object_name = class_names[class_id]
            category = classify_object(class_id)

            center_x = int((coordinate[0] + coordinate[2]) / 2)
            center_y = int((coordinate[1] + coordinate[3]) / 2)

            current_center = (center_x, center_y)

            if track_id in self.previous_centers:

                old_center = self.previous_centers[track_id]

                displacement = (
                    (center_x - old_center[0]) ** 2 +
                    (center_y - old_center[1]) ** 2
                ) ** 0.5

                movement_status = (
                    "Moving" if displacement > self.movement_threshold
                    else "Stationary"
                )

            else:
                old_center = None
                displacement = 0
                movement_status = "Initializing"

            self.previous_centers[track_id] = current_center

            object_data = {
                "track_id": track_id,
                "class_id": class_id,
                "object_name": object_name,
                "category": category,
                "confidence": round(confidence, 3),
                "coordinates": coordinate,
                "previous_center": old_center,
                "center": current_center,
                "displacement": round(displacement, 2),
                "movement": movement_status
            }

            frame_objects.append(object_data)

        return frame_objects