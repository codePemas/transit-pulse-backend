import asyncio
import websockets
import json

async def simulate_bus_journey():
    trip_id = 1  # Matches your active trip_id
    uri = f"ws://localhost:8000/ws/track/{trip_id}"
    
    # Simulated GPS path moving along coordinates
    path = [
        {"lat": -32.7875, "lng": 27.9116, "speed": 40.5, "heading": 90.0},
        {"lat": -32.7882, "lng": 27.9125, "speed": 45.0, "heading": 92.5},
        {"lat": -32.7890, "lng": 27.9140, "speed": 50.2, "heading": 95.0},
        {"lat": -32.7900, "lng": 27.9150, "speed": 38.0, "heading": 110.0},
        {"lat": -32.7920, "lng": 27.9175, "speed": 60.1, "heading": 85.4},
    ]

    print(f"Connecting to Transit Pulse WebSocket stream for Trip #{trip_id}...")
    async with websockets.connect(uri) as websocket:
        for point in path:
            payload = {
                "latitude": point["lat"],
                "longitude": point["lng"],
                "speed": point["speed"],
                "heading": point["heading"]
            }
            
            # Send GPS telemetry frame
            await websocket.send(json.dumps(payload))
            print(f"Sent GPS -> Lat: {point['lat']}, Lng: {point['lng']}, Speed: {point['speed']} km/h")
            
            # Wait 2 seconds before sending the next telemetry ping
            await asyncio.sleep(2)
            
            # Listen for broadcast response
            response = await websocket.recv()
            print(f"Broadcast received from server: {response}\n")

if __name__ == "__main__":
    asyncio.run(simulate_bus_journey())