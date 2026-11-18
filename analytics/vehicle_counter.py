class Vehicle_counter:
    def __init__(self, line_y = 300):
        self.line_y = line_y
        self.counted_ids = set()
        self.vehicle_count = 0
        self.pedestrian_count = 0
        self.other_count = 0

    def update(self,frame_objects):
        for obj in frame_objects:

            track_id = obj['track_id']
            previous_center = obj['previous_center']
            current_center = obj['center']

            if previous_center is None:
                continue

            old_y = previous_center[1]
            current_y = current_center[1]

            crossed_line = ((old_y < self.line_y <= current_y) or
                            (old_y >= self.line_y >= current_y))

            if crossed_line and track_id not in self.counted_ids:
                self.counted_ids.add(track_id)

                if obj['category'] == 'vehicle':
                    self.vehicle_count += 1

                elif obj['category'] == 'pedestrian':
                    self.pedestrian_count += 1
                else:
                    self.other_count += 1

    def get_statistics(self):
        return {
            "vehicles": self.vehicle_count,
                "pedestrians": self.pedestrian_count,
                "others":self.other_count,
                "total": (self.vehicle_count +
                            self.pedestrian_count +
                            self.other_count
                             )
        }