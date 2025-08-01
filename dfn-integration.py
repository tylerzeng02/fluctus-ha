import streamlit as st
import numpy as np
import sounddevice as sd
import scipy.signal as signal
import matplotlib.pyplot as plt
import soundfile as sf
import time
import threading
from scipy.signal import resample_poly
import torch
from df.enhance import enhance, init_df

st.set_page_config(page_title="10-Band Hearing Aid Equalizer", layout="centered")
st.title("DeepFilterNet Integration")

if "manual_denoise" not in st.session_state:
    st.session_state["manual_denoise"] = False
if "live_active" not in st.session_state:
    st.session_state["live_active"] = False

model, df_state, _ = init_df()

frequencies = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

presets = {
    "None (Manual)":          [0.0] * 10,
    "Presbycusis":            [0, 0, 0, 0, 2, 4, 6, 10, 12, 10],
    "Low-Frequency Loss":     [8, 6, 6, 4, 2, 0, 0, 0, 0, 0],
    "Noise-Induced Loss":     [0, 0, 0, 0, 0, 0, 4, 8, 6, 4],
    "Conductive Loss":        [10] * 10,
}

selected_preset = st.selectbox("Choose a Hearing Profile Preset", list(presets.keys()))
if st.button("Load Preset into Sliders"):
    for i, freq in enumerate(frequencies):
        st.session_state[f"slider_{freq}"] = presets[selected_preset][i]

gains = []
st.subheader("Equalizer Gain Settings (in dB)")
for i, freq in enumerate(frequencies):
    default = presets["None (Manual)"][i]
    gain = st.slider(
        f"{freq} Hz", -10.0, 60.0, value=st.session_state.get(f"slider_{freq}", default),
        step=1.0, key=f"slider_{freq}"
    )
    gains.append(gain)

st.markdown("### Current Gain Profile")
gain_display = {f"{freq} Hz": f"{g:.1f} dB" for freq, g in zip(frequencies, gains)}
st.table(gain_display)

manual_denoise = st.checkbox("Manually Enable DeepFilterNet", value=st.session_state["manual_denoise"])
st.session_state["manual_denoise"] = manual_denoise

st.markdown("## 2-Second Spectrogram Visualizer")
if st.button("Capture 2s Audio and Show Spectrograms"):
    fs = 44100
    duration = 2.0
    st.write("Capturing 2 seconds of microphone input...")
    audio = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='float32')
    sd.wait()
    audio = audio[:, 0]

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

    filters = create_filterbank(fs, gains)
    filtered = audio.copy()
    for b, a in filters:
        filtered = signal.lfilter(b, a, filtered)

    f1, t1, Sxx1 = signal.spectrogram(audio, fs)
    f2, t2, Sxx2 = signal.spectrogram(filtered, fs)

    fig, axs = plt.subplots(1, 2, figsize=(12, 4))
    axs[0].pcolormesh(t1, f1, 10 * np.log10(Sxx1 + 1e-10), shading='gouraud')
    axs[0].set_title("Original Audio")
    axs[0].set_ylabel("Frequency [Hz]")
    axs[0].set_xlabel("Time [s]")

    axs[1].pcolormesh(t2, f2, 10 * np.log10(Sxx2 + 1e-10), shading='gouraud')
    axs[1].set_title("After EQ")
    axs[1].set_xlabel("Time [s]")

    st.pyplot(fig)
if "live_stream" not in st.session_state:
    st.session_state["live_stream"] = None

if "stream_error" not in st.session_state:
    st.session_state["stream_error"] = None

manual_denoise_flag = False

def safe_resample(audio, orig_sr, target_sr):
    if orig_sr == target_sr:
        return audio
    gcd_val = np.gcd(orig_sr, target_sr)
    up = target_sr // gcd_val
    down = orig_sr // gcd_val
    return resample_poly(audio, up=up, down=down)

def create_filterbank(fs, gains):
    return [design_peaking_eq(fs, freq, gain) for freq, gain in zip(frequencies, gains)]

def process_live_audio(indata, outdata, frames, time_info, status):
    try:
        if status:
            if status.input_overflow:
                print("Input overflow detected")
            if status.output_underflow:
                print("Output underflow detected")

        audio = indata[:, 0].copy()
        fs = 44100

        filtered = audio.copy()
        filters = create_filterbank(fs, gains)
        for b, a in filters:
            filtered = signal.lfilter(b, a, filtered)

        apply_denoise = manual_denoise_flag or st.session_state.get("manual_denoise", False)

        if apply_denoise:
            try:
                audio_48k = safe_resample(filtered, orig_sr=fs, target_sr=48000)
                audio_48k = signal.medfilt(audio_48k, kernel_size=3)
                audio_tensor = torch.tensor(audio_48k, dtype=torch.float32).view(1, -1)
                with torch.no_grad():
                    enhanced = enhance(model, df_state, audio_tensor).squeeze().numpy()
                enhanced = signal.medfilt(enhanced, kernel_size=3)
                filtered = safe_resample(enhanced, orig_sr=48000, target_sr=fs)

                if np.max(np.abs(filtered)) > 0:
                    orig_max = np.max(np.abs(audio))
                    new_max = np.max(np.abs(filtered))
                    scale_factor = min(orig_max / new_max, 2.0) if new_max > 0 else 1.0
                    filtered = filtered * scale_factor

            except Exception as e:
                print(f"[ERROR] DeepFilterNet processing failed: {e}")

        max_amp = np.max(np.abs(filtered))
        if max_amp > 0.95:
            filtered = filtered * (0.95 / max_amp)

        n_samples = min(len(filtered), outdata.shape[0])
        outdata[:n_samples, 0] = filtered[:n_samples]

        if n_samples < outdata.shape[0]:
            outdata[n_samples:, 0] = 0.0

    except Exception as e:
        st.session_state["stream_error"] = str(e)
        print(f"[ERROR] Audio callback failed: {e}")
        outdata.fill(0)

col1, col2 = st.columns(2)

if col1.button("Start Live Hearing Aid"):
    if not st.session_state["live_active"]:
        try:
            st.session_state["stream_error"] = None
            manual_denoise_flag = st.session_state["manual_denoise"]

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
            st.success("Live hearing aid started.")

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
                print(f"[WARN] Error stopping stream: {e}")
            st.session_state["live_stream"] = None
        st.warning("Live hearing aid stopped.")

if st.session_state["live_active"]:
    st.markdown("Live hearing aid is running")
    if st.session_state["manual_denoise"]:
        st.info("DeepFilterNet manually enabled")
    else:
        st.info("DeepFilterNet inactive")

    if st.session_state.get("stream_error"):
        st.error(f"Stream error: {st.session_state['stream_error']}")
        st.warning("Try stopping and restarting the hearing aid.")
