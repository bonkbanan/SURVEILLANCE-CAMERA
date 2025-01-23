import cv2
import RPi.GPIO as GPIO
import time
from telegram import Bot
from telegram.ext import Updater, CommandHandler
from telegram.error import TelegramError
import asyncio
import os
import concurrent.futures
import math

BOT_TOKEN = "Telegram_TOKEN"
CHAT_ID = "...."  # Replace with your chat ID (or use dynamic fetching)
bot = Bot(token=BOT_TOKEN)

# Ensure directory exists
PICTURE_DIR = "saved_pictures"
os.makedirs(PICTURE_DIR, exist_ok=True)  # Create folder if it doesn't exist

# Налаштування пінів для крокового двигуна
step_pins = [26, 19, 13, 6]  # Заміни на свої піни

current_servo_angle = 90  # Початковий кут сервоприводу (прямо)
last_servo_update = 0     # Час останнього руху сервоприводу
servo_update_interval = 1  # Інтервал оновлення сервоприводу (секунди)

last_camera_update = 0     #Час останнього руху camera
camera_update_interval = 30  # Інтервал оновлення camera (секунди)

GPIO.setmode(GPIO.BCM)
for pin in step_pins:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)

# Сервопривід
servo_pin = 17
GPIO.setup(servo_pin, GPIO.OUT)
servo_pwm = GPIO.PWM(servo_pin, 50)
servo_pwm.start(7.5)
servo_pwm.ChangeDutyCycle(0)
saved_faces = []  # List of previously detected face centers (for "once-per-person")



# Послідовність для крокового двигуна
step_seq = [
    [1, 0, 0, 1],
    [1, 0, 0, 0],
    [1, 1, 0, 0],
    [0, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 0],
    [0, 0, 1, 1],
    [0, 0, 0, 1],
]

executor = concurrent.futures.ThreadPoolExecutor()


def step_motor(steps, direction=1):
    step_count = len(step_seq)
    for _ in range(abs(steps)):
        for step in range(step_count):
            for pin in range(4):
                GPIO.output(step_pins[pin], step_seq[step][pin] if direction > 0 else step_seq[-step-1][pin])
            time.sleep(0.0005)  # Швидкість обертання


async def step_motor_async(steps, direction=1, step_delay=0.001):
    await asyncio.get_running_loop().run_in_executor(executor, step_motor, steps, direction)

semaphore = asyncio.Semaphore(5)  # Adjust based on workload


async def send_photo_async(frame, retries = 3, delay = 5):
    try:
        async with semaphore:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            #filename = f"face_{timestamp}.jpg"
            filename = os.path.join(PICTURE_DIR, f"face_{timestamp}.jpg")  # Save to folder

            cv2.imwrite(filename, frame)
            for attempt in range(retries):
                try:
                    with open(filename, 'rb') as photo:
                        await bot.send_photo(chat_id=CHAT_ID, photo=photo)
                    print("Photo sent successfully!")
                    return  # Exit if successful
                except Exception as e:
                    print(f"Attempt {attempt + 1} failed: {e}")
                    if attempt < retries - 1:
                        print(f"Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                    else:
                        print("All retry attempts failed.")
                except TelegramError as e:
                    print(f"Telegram API Error: {e}")
    except Exception as e:
        print(f"send_photo_saync failed: {e}")

async def move_servo(angle):
    global current_servo_angle, last_servo_update
    angle = max(45, min(110, angle))  # Обмежуємо кут між 45 і 120 градусів

    # Перевірка, чи потрібно змінювати кут
    if time.time() - last_servo_update > servo_update_interval:
        current_servo_angle = angle
       	duty = (angle / 18) + 2.5
       	servo_pwm.ChangeDutyCycle(duty)
        await asyncio.sleep(0.001)
        last_servo_update = time.time()  # Оновлення часу останнього руху
        servo_pwm.ChangeDutyCycle(0)
async def main():
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    cap = cv2.VideoCapture(0)
    global last_camera_update, camera_update_interval
    try:
        while True:
            ret, frame = cap.read()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.1, 10)

            if len(faces) > 0:
                (x, y, w, h) = faces[0]
                center_x = x + w // 2
                center_y = y + h // 2
                frame_center_x = frame.shape[1] // 2
                frame_center_y = frame.shape[0] // 2
                face_center = (center_x, center_y)
                print("face detected at x:",center_x," ,y:", center_y) 
                
                    
                # Save the photo 
                if time.time() - last_camera_update > camera_update_interval:
                    asyncio.create_task(send_photo_async(frame))
                    #await send_photo_async(frame)
                    last_camera_update = time.time()
                
                diff_x = center_x - frame_center_x
                diff_y = center_y - frame_center_y

                if abs(diff_x) > 20:
                    asyncio.create_task(step_motor_async(math.ceil(abs(diff_x/7)), direction=-1 if diff_x > 0 else 1))

                if abs(diff_y) > 15:
                    angle_change = -diff_y // 15
                    target_angle = current_servo_angle + angle_change
                    asyncio.create_task(move_servo(90 + angle_change))
                    
                # Visual feedback on video feed
                cv2.circle(frame, (center_x, center_y), 5, (0, 255, 0), 2)
                cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 0, 0), 2)
            else:
                for pin in step_pins:
                    GPIO.output(pin, 0)
            # Display the frame
            cv2.rectangle(frame, (640 // 2 - 30, 480 // 2 - 30), (640 // 2 + 30, 480 // 2 + 30), (255, 255, 255), 2)
            cv2.imshow('Face Tracking', frame)

            await asyncio.sleep(0.01)
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        print("Зупинка програми...")
        servo_pwm.stop()
        GPIO.cleanup()
    finally:
        cap.release()
        cv2.destroyAllWindows()
        servo_pwm.stop()
        GPIO.cleanup()

asyncio.run(main())

