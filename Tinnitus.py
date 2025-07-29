import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wav

def generate_pink_noise(duration, sample_rate=44100):
    white_noise = np.random.randn(int(duration * sample_rate))

    b = np.array([0.02109238, 0.07113478, 0.07113478, 0.02109238])
    a = np.array([1.0, -1.941, 1.221, -0.225])
    
    pink_noise = np.zeros_like(white_noise)
    pink_noise[0] = white_noise[0]
    for i in range(1, len(white_noise)):
        pink_noise[i] = b[0] * white_noise[i] + b[1] * white_noise[i-1] + b[2] * white_noise[i-2] + b[3] * white_noise[i-3] - a[1] * pink_noise[i-1] - a[2] * pink_noise[i-2] - a[3] * pink_noise[i-3]

    pink_noise /= np.max(np.abs(pink_noise))
    
    return pink_noise
  
def audio_callback(indata, outdata, frames, time, status):
    if status:
        print(status)
    
    pink_noise = generate_pink_noise(frames / 44100)
    
    mixed_audio = indata[:, 0] * 0.2 + pink_noise[:frames] * 0.005  

    mixed_audio = np.clip(mixed_audio, -1, 1)

    outdata[:, 0] = mixed_audio

    if time.inputBufferAdcTime > 5.0:
        print("Recording the input audio...")
        wav.write("input_audio_with_pink_noise.wav", 44100, (indata * 32767).astype(np.int16))

def start_live_audio():
    sample_rate = 44100
    duration = 10  
    channels = 1 
    blocksize = 1024  
    
    with sd.Stream(callback=audio_callback, channels=channels, samplerate=sample_rate, blocksize=blocksize):
        print("Press Ctrl+C to stop recording...")
        sd.sleep(duration * 1000)

if __name__ == "__main__":
    start_live_audio()
