# Intelligent Traffic Control

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/) 
[![OpenCV](https://img.shields.io/badge/OpenCV-Computer%20Vision-green.svg)](https://opencv.org/)
[![Ultralytics](https://img.shields.io/badge/Ultralytics-YOLO-orange.svg)](https://www.ultralytics.com/)

An intelligent computer vision system for real-time traffic monitoring and congestion detection. The application uses a webcam, YOLO object detection and tracking, virtual counting lines, and road-region analysis to identify vehicles, pedestrians, and other objects.

## Features

- Real-time vehicle, pedestrian, and object detection
- Persistent object tracking with the YOLO/ByteTrack tracker
- Vehicle movement classification as **Moving**, **Stationary**, or **Initializing**
- Configurable virtual counting line for crossing counts
- Polygon-based road-region filtering
- Low, moderate, and high congestion estimation based on vehicle density and stationary traffic
- Live annotated video output

## How It Works

1. Captures frames from the default webcam.
2. Runs YOLO object detection and tracking on each frame.
3. Calculates object centers and tracks movement between frames.
4. Filters tracked objects inside the configured road polygon.
5. Counts objects that cross the virtual counting line.
6. Estimates congestion over a 10-second observation window.
7. Displays detections, tracking IDs, movement status, counts, and congestion level in the video window.

## Project Structure

```text
Intelligent_Traffic_Control/
├── analytics/
│   ├── congestion.py       # Time-window congestion analysis
│   ├── road_region.py      # Polygon-based road-region filtering
│   └── vehicle_counter.py  # Virtual-line object counting
├── camera/
│   └── webcam.py           # Webcam capture and frame flipping
├── detection/
│   ├── object_classifier.py # Vehicle, pedestrian, or other classification
│   └── vehicle_detector.py # YOLO detection and tracking
├── tracking/
│   └── vehicle_tracker.py  # Track IDs and movement analysis
├── main.py                 # Application entry point
├── requirements.txt
└── .gitignore
```

## Requirements

- Python 3.10 or newer
- A webcam
- Internet access on first run so Ultralytics can download `yolo11n.pt`

Python dependencies:

- `opencv-python`
- `pandas`
- `numpy`
- `ultralytics`

## Installation

Clone the repository:

```bash
git clone https://github.com/enoch-jnr/Intelligent_Traffic_Control.git
cd Intelligent_Traffic_Control
```

Create and activate a virtual environment:

```bash
python -m venv .venv
```

**Windows:**

```bash
.venv\Scripts\activate
```

**Linux/macOS:**

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

Run the application:

```bash
python main.py
```

Press **Q** to stop the application and close the video window.

## Configuration

The default webcam index is `0`. To use another camera, change:

```python
camera = Webcam()
```

to:

```python
camera = Webcam(camera_index=1)
```

The road region is defined in `main.py` using normalized coordinates. Adjust the polygon points when using a different camera angle or resolution:

```python
road_region = RoadRegion([
    (0.1, 0.3),
    (0.9, 0.3),
    (1.0, 0.95),
    (0.0, 0.95)
])
```

The virtual counting line defaults to `300` pixels from the top:

```python
counter = Vehicle_counter()
```

Congestion is analyzed using:

- a 10-second observation window
- 3 vehicles for the moderate threshold
- 8 vehicles for the high threshold
- the proportion of stationary vehicles

These values can be changed by creating a `CongestionAnalyzer` with custom parameters:

```python
CongestionAnalyzer(
    window_seconds=10,
    moderate_threshold=3,
    high_threshold=8,
    stationary_threshold=0.5
)
```

## Output

The video window displays:

- Detected object boxes
- Unique tracking IDs
- Movement status
- A counting line
- A road-region polygon
- Vehicle, pedestrian, and other-object totals
- Current congestion level

## Limitations

- Accuracy depends on lighting, camera quality, and viewing angle.
- The result may be affected by occlusion, shadows, or dense traffic.
- Congestion estimates are heuristic and not calibrated for all road conditions.
- The application currently uses the default webcam and is designed for live visualization.

## Contributing

Contributions are welcome. Fork the repository, create a feature branch, make your changes, and open a pull request.

## License

No license is currently specified for this repository.
