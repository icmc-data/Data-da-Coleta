
import os
from ultralytics import YOLO
import kagglehub
import ultralytics

ultralytics.checks()

path = kagglehub.dataset_download("vencerlanz09/taco-dataset-yolo-format")
print("Path to dataset files:", path)

model = YOLO("yolo12s.pt")

model.train(
    data=f"{path}/data.yaml",
    epochs=1000,
    batch=16,
    imgsz=416,
    optimizer='AdamW',
    lr0=1e-3,  # Initial learning rate
    weight_decay=1e-4,
    save=True,  # Save the best model
    save_period=25,  # Save model every 10 epochs
    val=True,  # Evaluate on validation set
    plots=True,  # Plot training results
    device='0',  # Use GPU if available
    patience=100 # Early stopping patience                                                                                                                                                                                                                        
)




