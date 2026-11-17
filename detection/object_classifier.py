
OBJECT_CATEGORIES = {
    "pedestrian": [0],
    "non_motorized_transport":[1],
    "vehicle":[2,3,5,7],
    "traffic_infrastructure":[9,11],
    "animals":[16,17]

}

def classify_object(class_id):
    for category, class_ids in OBJECT_CATEGORIES.items():
        if class_id in class_ids:
            return category
    return "other"