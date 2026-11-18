import cv2
import numpy as np


class RoadRegion:

    def __init__(self, points):
        self.points = points

    def get_polygon(self, frame_shape):

        height, width = frame_shape[:2]

        polygon = [
            (int(x * width), int(y * height))
            for x, y in self.points
        ]

        return np.array(polygon, dtype=np.int32)

    def contains(self, center, frame_shape):

        polygon = self.get_polygon(frame_shape)

        result = cv2.pointPolygonTest(
            polygon,
            center,
            False
        )

        return result >= 0

    def draw(self, frame):

        polygon = self.get_polygon(frame.shape)

        cv2.polylines(
            frame,
            [polygon],
            True,
            (255, 0, 0),
            2
        )

        return frame