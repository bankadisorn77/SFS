import argparse
import json
import sys
import paho.mqtt.publish as publish


def parse_args():
    parser = argparse.ArgumentParser(
        description="CLI tool to publish custom MQTT topic and payload"
    )

    parser.add_argument(
        "-t",
        "--topic",
        type=str,
        required=True,
        help="MQTT Topic (e.g. SFS_YOKA_POKE/UTL-Ai-SFS(Dev)/control)",
    )

    parser.add_argument(
        "-p",
        "--payload",
        type=str,
        help='JSON Payload string (e.g. \'{"enable": true, "camera_id": "cam_1"}\')',
    )

    parser.add_argument(
        "-b",
        "--broker",
        type=str,
        default="169.254.26.65",
        help="MQTT Broker IP (default: 169.254.26.65)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1883,
        help="MQTT Broker Port (default: 1883)",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.payload:
        try:
            parsed_payload = json.loads(args.payload)
            payload_str = json.dumps(parsed_payload)
        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON payload: {e}")
            sys.exit(1)
    else:
        payload_str = args.payload

    print(f"Connecting to Broker: {args.broker}:{args.port}")
    print(f"Topic:   {args.topic}")
    print(f"Payload: {payload_str}")

    try:
        publish.single(
            topic=args.topic,
            payload=payload_str,
            hostname=args.broker,
            port=args.port,
        )
        print("-> Published successfully!")
    except Exception as e:
        print(f"[ERROR] Failed to publish message: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()