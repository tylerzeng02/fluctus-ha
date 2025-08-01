import streamlit as st
import numpy as np
import sounddevice as sd
import scipy.signal as signal
import matplotlib.pyplot as plt
import soundfile as sf
import time
import threading
import queue
import tempfile
import subprocess
import base64
import os
from scipy.signal import resample_poly
import torch
from df.enhance import enhance, init_df

st.set_page_config(page_title="Fluctus Hearing Aid", layout="wide")
st.title("Fluctus Hearing Aid")

if "manual_denoise" not in st.session_state:
    st.session_state["manual_denoise"] = False
if "voicefixer_enabled" not in st.session_state:
    st.session_state["voicefixer_enabled"] = False
if "live_active" not in st.session_state:
    st.session_state["live_active"] = False
if "live_stream" not in st.session_state:
    st.session_state["live_stream"] = None
if "stream_error" not in st.session_state:
    st.session_state["stream_error"] = None

@st.cache_resource
def load_models():
    model, df_state, _ = init_df()
    try:
        from voicefixer import VoiceFixer
        voicefixer = VoiceFixer()
        return model, df_state, voicefixer
    except ImportError:
        st.warning("VoiceFixer not available")
        return model, df_state, None

model, df_state, voicefixer = load_models()

frequencies = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

presets = {
    "None (Manual)": [0.0] * 10,
    "Presbycusis": [0, 0, 0, 0, 2, 4, 6, 10, 12, 10],
    "Low-Frequency Loss": [8, 6, 6, 4, 2, 0, 0, 0, 0, 0],
    "Noise-Induced Loss": [0, 0, 0, 0, 0, 0, 4, 8, 6, 4],
    "Conductive Loss": [10] * 10,
}

selected_preset = st.selectbox("Choose a Hearing Profile Preset", list(presets.keys()))
if st.button("Load Preset"):
    for i, freq in enumerate(frequencies):
        st.session_state[f"slider_{freq}"] = presets[selected_preset][i]

gains = []
st.subheader("Equalizer Settings")
for i, freq in enumerate(frequencies):
    default = presets["None (Manual)"][i]
    gain = st.slider(
        f"{freq} Hz", -10.0, 60.0, 
        value=st.session_state.get(f"slider_{freq}", default),
        step=1.0, key=f"slider_{freq}"
    )
    gains.append(gain)

manual_denoise = st.checkbox("Enable DeepFilterNet", value=st.session_state["manual_denoise"])
st.session_state["manual_denoise"] = manual_denoise

voicefixer_enabled = st.checkbox("Enable VoiceFixer", value=st.session_state["voicefixer_enabled"])
st.session_state["voicefixer_enabled"] = voicefixer_enabled

def safe_resample(audio, orig_sr, target_sr):
    if orig_sr == target_sr:
        return audio
    gcd_val = np.gcd(orig_sr, target_sr)
    up = target_sr // gcd_val
    down = orig_sr // gcd_val
    return resample_poly(audio, up=up, down=down)

def design_peaking_eq(fs, center_freq, gain_db, Q=1.0):
    A = 10 ** (gain_db / 40)
    omega = 2 * np.pi * center_freq / fs
    alpha = np.sin(omega) / (2 * Q)
    b0 = 1 + alpha * A
    b1 = -2 * np.cos(omega)
    b2 = 1 - alpha * A
    a0 = 1 + alpha / A
    a1 = -2 * np.cos(omega)
    a2 = 1 - alpha / A
    b = np.array([b0, b1, b2]) / a0
    a = np.array([a0, a1, a2]) / a0
    return b, a

def create_filterbank(fs, gains):
    return [design_peaking_eq(fs, freq, gain) for freq, gain in zip(frequencies, gains)]

def process_with_voicefixer(audio, fs):
    if not voicefixer or not st.session_state["voicefixer_enabled"]:
        return audio
    
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as input_file:
            input_path = input_file.name
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as output_file:
            output_path = output_file.name
        
        sf.write(input_path, audio, fs)
        voicefixer.restore(input_path, output_path, 0)
        enhanced_audio, _ = sf.read(output_path)
        
        os.unlink(input_path)
        os.unlink(output_path)
        
        return enhanced_audio
    except Exception as e:
        print(f"VoiceFixer error: {e}")
        return audio

def process_with_deepfilternet(audio, fs):
    if not st.session_state["manual_denoise"]:
        return audio
    
    try:
        audio_48k = safe_resample(audio, orig_sr=fs, target_sr=48000)
        audio_tensor = torch.tensor(audio_48k, dtype=torch.float32).view(1, -1)
        with torch.no_grad():
            enhanced = enhance(model, df_state, audio_tensor).squeeze().numpy()
        enhanced = safe_resample(enhanced, orig_sr=48000, target_sr=fs)
        
        if np.max(np.abs(enhanced)) > 0:
            orig_max = np.max(np.abs(audio))
            new_max = np.max(np.abs(enhanced))
            scale_factor = min(orig_max / new_max, 2.0) if new_max > 0 else 1.0
            enhanced = enhanced * scale_factor
        
        return enhanced
    except Exception as e:
        print(f"DeepFilterNet error: {e}")
        return audio

def apply_equalizer(audio, fs, gains):
    try:
        filters = create_filterbank(fs, gains)
        filtered = audio.copy()
        for b, a in filters:
            filtered = signal.lfilter(b, a, filtered)
        return filtered
    except Exception as e:
        print(f"EQ error: {e}")
        return audio

def process_live_audio(indata, outdata, frames, time_info, status):
    try:
        if status:
            if status.input_overflow:
                print("Input overflow")
            if status.output_underflow:
                print("Output underflow")

        audio = indata[:, 0].copy()
        fs = 44100

        processed = process_with_voicefixer(audio, fs)
        processed = process_with_deepfilternet(processed, fs)
        processed = apply_equalizer(processed, fs, gains)

        max_amp = np.max(np.abs(processed))
        if max_amp > 0.95:
            processed = processed * (0.95 / max_amp)

        n_samples = min(len(processed), outdata.shape[0])
        outdata[:n_samples, 0] = processed[:n_samples]

        if n_samples < outdata.shape[0]:
            outdata[n_samples:, 0] = 0.0

    except Exception as e:
        st.session_state["stream_error"] = str(e)
        print(f"Audio callback error: {e}")
        outdata.fill(0)

col1, col2 = st.columns(2)

if col1.button("Start Live Hearing Aid"):
    if not st.session_state["live_active"]:
        try:
            st.session_state["stream_error"] = None

            st.session_state["live_stream"] = sd.Stream(
                channels=1,
                samplerate=44100,
                blocksize=4096,
                latency=0.3,
                dtype='float32',
                callback=process_live_audio
            )

            st.session_state["live_stream"].start()
            st.session_state["live_active"] = True
            st.success("Live hearing aid started")

        except Exception as e:
            st.error(f"Error starting hearing aid: {e}")
            if st.session_state.get("live_stream"):
                try:
                    st.session_state["live_stream"].stop()
                    st.session_state["live_stream"].close()
                except:
                    pass
                st.session_state["live_stream"] = None
            st.session_state["live_active"] = False

if col2.button("Stop Live Hearing Aid"):
    if st.session_state["live_active"]:
        st.session_state["live_active"] = False
        time.sleep(0.6)
        if st.session_state.get("live_stream") is not None:
            try:
                st.session_state["live_stream"].stop()
                st.session_state["live_stream"].close()
            except Exception as e:
                print(f"Error stopping stream: {e}")
            st.session_state["live_stream"] = None
        st.warning("Live hearing aid stopped")

if st.session_state["live_active"]:
    st.markdown("🔴 Live hearing aid is running")
    status_items = []
    if st.session_state["voicefixer_enabled"]:
        status_items.append("VoiceFixer: ON")
    if st.session_state["manual_denoise"]:
        status_items.append("DeepFilterNet: ON")
    status_items.append("Equalizer: ON")
    st.info(" | ".join(status_items))

    if st.session_state.get("stream_error"):
        st.error(f"Stream error: {st.session_state['stream_error']}")

st.markdown("## Test Audio Processing")
if st.button("Capture 2s Audio and Show Spectrograms"):
    fs = 44100
    duration = 2.0
    st.write("Capturing audio...")
    audio = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='float32')
    sd.wait()
    audio = audio[:, 0]

    processed = process_with_voicefixer(audio, fs)
    processed = process_with_deepfilternet(processed, fs)
    processed = apply_equalizer(processed, fs, gains)

    f1, t1, Sxx1 = signal.spectrogram(audio, fs)
    f2, t2, Sxx2 = signal.spectrogram(processed, fs)

    fig, axs = plt.subplots(1, 2, figsize=(12, 4))
    axs[0].pcolormesh(t1, f1, 10 * np.log10(Sxx1 + 1e-10), shading='gouraud')
    axs[0].set_title("Original Audio")
    axs[0].set_ylabel("Frequency [Hz]")
    axs[0].set_xlabel("Time [s]")

    axs[1].pcolormesh(t2, f2, 10 * np.log10(Sxx2 + 1e-10), shading='gouraud')
    axs[1].set_title("Processed Audio")
    axs[1].set_xlabel("Time [s]")

    st.pyplot(fig)
