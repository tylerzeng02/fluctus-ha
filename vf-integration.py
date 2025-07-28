import os
import sys
import threading
import queue
import tempfile
import subprocess
import soundfile as sf
import numpy as np
import streamlit as st
import base64

st.set_page_config(page_title="VoiceFixer Integration", layout="wide")

@st.cache_resource
def load_voicefixer():
    from voicefixer import VoiceFixer
    return VoiceFixer()

def record_audio_continuous(q, stop_event, sample_rate=16000):
    while not stop_event.is_set(): 
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_path = temp_file.name
        if os.name == 'nt':
            cmd = f'sox -d -r {sample_rate} -c 1 -b 16 {temp_path} trim 0 3'
        else:
            cmd = f'sox -d -r {sample_rate} -c 1 -b 16 {temp_path} trim 0 3'
        subprocess.run(cmd, shell=True)
        q.put(temp_path)

def main():
    st.title("VoiceFixer Continuous Streaming")
    if 'streaming' not in st.session_state:
        st.session_state.streaming = False
    voicefixer = load_voicefixer()
    q = queue.Queue()
    stop_event = threading.Event()
    if st.button("Start Streaming"):
        st.session_state.streaming = True
        record_thread = threading.Thread(target=record_audio_continuous, args=(q, stop_event), daemon=True)
        record_thread.start()
    if st.button("Stop Streaming"):
        st.session_state.streaming = False
        stop_event.set()
    while st.session_state.streaming:
        if not q.empty():
            input_path = q.get()
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as output_file:
                output_path = output_file.name
            voicefixer.restore(input_path, output_path, 0)
            with open(output_path, 'rb') as f:
                audio_bytes = f.read()
            audio_base64 = base64.b64encode(audio_bytes).decode()
            audio_url = f"data:audio/wav;base64,{audio_base64}"
            st.markdown(f"""
                <audio src="{audio_url}" autoplay hidden></audio>
            """, unsafe_allow_html=True)
            os.unlink(input_path)
            os.unlink(output_path)

if __name__ == "__main__":
    main()
