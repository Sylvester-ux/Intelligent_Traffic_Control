from ultralytics import YOLO

class VehicleDetector:
    def __init__(self, model_path='yolo11n.pt'):
        self.model = YOLO(model_path)

    def detect(self, frame):
        results = self.model.track(frame,
                                   conf=0.3,
                                   verbose=False,
                                   persist=True,
                                   tracker="bytetrack.yaml")
        return results