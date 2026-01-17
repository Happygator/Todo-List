import socket
import sys

def shutdown_bot():
    try:
        # Connect to the bot's lock port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", 60001))
        sock.sendall(b"SHUTDOWN")
        sock.close()
        print("Shutdown signal sent to the bot.")
    except ConnectionRefusedError:
        print("Could not connect to the bot. Is it running?")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    shutdown_bot()
