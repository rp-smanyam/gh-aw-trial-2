class RealtimeDemo {
    constructor() {
        this.ws = null;
        this.isConnected = false;
        this.isMuted = false;
        this.isCapturing = false;
        this.audioContext = null;
        this.processor = null;
        this.stream = null;
        this.sessionId = this.generateSessionId();

        // Audio playback queue
        this.audioQueue = [];
        this.isPlayingAudio = false;
        this.playbackAudioContext = null;
        this.currentAudioSource = null;

        this.initializeElements();
        this.setupEventListeners();
    }

    initializeElements() {
        this.connectBtn = document.getElementById('connectBtn');
        this.muteBtn = document.getElementById('muteBtn');
        this.status = document.getElementById('status');
        this.messagesContent = document.getElementById('messagesContent');
        this.eventsContent = document.getElementById('eventsContent');
        this.toolsContent = document.getElementById('toolsContent');
        this.agentSelect = document.getElementById('agentSelect');
        this.jsonEditor = document.getElementById('jsonEditor');
        this.resetBtn = document.getElementById('resetBtn');
        this.textInput = document.getElementById('textInput');
        this.sendBtn = document.getElementById('sendBtn');
        this.sessionIdDisplay = document.getElementById('sessionIdDisplay');
    }

    setupEventListeners() {
        this.connectBtn.addEventListener('click', () => {
            if (this.isConnected) {
                this.disconnect();
            } else {
                this.connect();
            }
        });

        this.muteBtn.addEventListener('click', () => {
            this.toggleMute();
        });

        this.agentSelect.addEventListener('change', () => {
            this.updateJsonProduct();
        });

        this.resetBtn.addEventListener('click', () => {
            this.resetChatSessionId(true);
        });

        this.sendBtn.addEventListener('click', () => {
            this.sendTextMessage();
        });

        this.textInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.sendTextMessage();
            }
        });
    }

    updateJsonProduct() {
        try {
            const jsonText = this.jsonEditor.value.trim();
            const payload = JSON.parse(jsonText);
            payload.product = this.agentSelect.value;
            this.jsonEditor.value = JSON.stringify(payload, null, 2);
        } catch (error) {
            console.error('Failed to update product in JSON:', error);
        }
    }

    generateUUID() {
        return 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'.replace(/x/g, () => {
            return Math.floor(Math.random() * 16).toString(16);
        });
    }

    resetChatSessionId(forceUpdateUI = false) {
        try {
            const jsonText = this.jsonEditor.value.trim();
            const payload = JSON.parse(jsonText);
            const newId = this.generateUUID();
            payload.chat_session_id = newId;
            this.jsonEditor.value = JSON.stringify(payload, null, 2);
            if (this.sessionIdDisplay) {
                this.sessionIdDisplay.textContent = newId;
            }
            if (forceUpdateUI) {
                this.jsonEditor.dispatchEvent(new Event('input', { bubbles: true }));
            }
        } catch (error) {
            console.error('Failed to reset chat_session_id:', error);
        }
    }

    generateSessionId() {
        return 'session_' + Math.random().toString(36).substr(2, 9);
    }

    async connect() {
        try {
            const selectedAgent = this.agentSelect.value;
            this.ws = new WebSocket(`ws://0.0.0.0:8000/voice-ui/websocket/${this.sessionId}?agent_name=${selectedAgent}`);

            this.ws.onopen = async () => {
                // Refresh chat_session_id on connect so each session is unique.
                this.resetChatSessionId();
                // Send the AskRequest JSON as the first message and wait for it to be sent
                const payloadSent = this.sendAskRequestPayload();
                if (!payloadSent) {
                    return; // Connection will be closed by sendAskRequestPayload on error
                }
                
                // Small delay to ensure server processes the ask_request before audio starts
                await new Promise(resolve => setTimeout(resolve, 100));
                
                this.isConnected = true;
                this.updateConnectionUI();
                this.startContinuousCapture();
            };

            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleRealtimeEvent(data);
            };

            this.ws.onclose = () => {
                this.isConnected = false;
                this.updateConnectionUI();
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };

        } catch (error) {
            console.error('Failed to connect:', error);
        }
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
        }
        this.stopContinuousCapture();
    }

    updateConnectionUI() {
        if (this.isConnected) {
            this.connectBtn.textContent = 'Disconnect';
            this.connectBtn.className = 'connect-btn connected';
            this.status.textContent = 'Connected';
            this.status.className = 'status connected';
            this.muteBtn.disabled = false;
            this.textInput.disabled = false;
            this.sendBtn.disabled = false;
        } else {
            this.connectBtn.textContent = 'Connect';
            this.connectBtn.className = 'connect-btn disconnected';
            this.status.textContent = 'Disconnected';
            this.status.className = 'status disconnected';
            this.muteBtn.disabled = true;
            this.textInput.disabled = true;
            this.sendBtn.disabled = true;
        }
    }

    sendTextMessage() {
        const text = this.textInput.value.trim();
        if (!text || !this.isConnected || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
        }

        this.ws.send(JSON.stringify({
            type: 'text',
            data: text
        }));

        this.textInput.value = '';
        console.log('Sent text message:', text);
    }

    toggleMute() {
        this.isMuted = !this.isMuted;
        this.updateMuteUI();
    }

    updateMuteUI() {
        if (this.isMuted) {
            this.muteBtn.textContent = '🔇 Mic Off';
            this.muteBtn.className = 'mute-btn muted';
        } else {
            this.muteBtn.textContent = '🎤 Mic On';
            this.muteBtn.className = 'mute-btn unmuted';
            if (this.isCapturing) {
                this.muteBtn.classList.add('active');
            }
        }
    }

    async startContinuousCapture() {
        if (!this.isConnected || this.isCapturing) return;

        // Check if getUserMedia is available
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error('getUserMedia not available. Please use HTTPS or localhost.');
        }

        try {
            this.stream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    sampleRate: 24000,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true
                } 
            });

            this.audioContext = new AudioContext({ sampleRate: 24000 });
            const source = this.audioContext.createMediaStreamSource(this.stream);

            // Create a script processor to capture audio data
            this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);
            source.connect(this.processor);
            this.processor.connect(this.audioContext.destination);

            this.processor.onaudioprocess = (event) => {
                if (!this.isMuted && this.ws && this.ws.readyState === WebSocket.OPEN) {
                    const inputBuffer = event.inputBuffer.getChannelData(0);
                    const int16Buffer = new Int16Array(inputBuffer.length);

                    // Convert float32 to int16
                    for (let i = 0; i < inputBuffer.length; i++) {
                        int16Buffer[i] = Math.max(-32768, Math.min(32767, inputBuffer[i] * 32768));
                    }

                    this.ws.send(JSON.stringify({
                        type: 'audio',
                        data: Array.from(int16Buffer)
                    }));
                }
            };

            this.isCapturing = true;
            this.updateMuteUI();

        } catch (error) {
            console.error('Failed to start audio capture:', error);
        }
    }

    stopContinuousCapture() {
        if (!this.isCapturing) return;

        this.isCapturing = false;

        if (this.processor) {
            this.processor.disconnect();
            this.processor = null;
        }

        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }

        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }

        this.updateMuteUI();
    }

    handleRealtimeEvent(event) {
        // Add to raw events pane
        this.addRawEvent(event);

        // Add to tools panel if it's a tool or handoff event
        if (event.type === 'tool_start' || event.type === 'tool_end' || event.type === 'handoff') {
            this.addToolEvent(event);
        }

        // Handle specific event types
        switch (event.type) {
            case 'audio':
                this.playAudio(event.audio);
                break;
            case 'audio_interrupted':
                this.stopAudioPlayback();
                break;
            case 'history_updated':
                this.updateMessagesFromHistory(event.history);
                break;
        }
    }


    updateMessagesFromHistory(history) {
        console.log('updateMessagesFromHistory called with:', history);

        // Clear all existing messages
        this.messagesContent.innerHTML = '';

        // Add messages from history
        if (history && Array.isArray(history)) {
            console.log('Processing history array with', history.length, 'items');
            history.forEach((item, index) => {
                console.log(`History item ${index}:`, item);
                if (item.type === 'message') {
                    const role = item.role;
                    let content = '';

                    console.log(`Message item - role: ${role}, content:`, item.content);

                    if (item.content && Array.isArray(item.content)) {
                        // Extract text from content array
                        item.content.forEach(contentPart => {
                            console.log('Content part:', contentPart);
                            if (contentPart.type === 'text' && contentPart.text) {
                                content += contentPart.text;
                            } else if (contentPart.type === 'input_text' && contentPart.text) {
                                content += contentPart.text;
                            } else if (contentPart.type === 'input_audio' && contentPart.transcript) {
                                content += contentPart.transcript;
                            } else if (contentPart.type === 'audio' && contentPart.transcript) {
                                content += contentPart.transcript;
                            }
                        });
                    }

                    console.log(`Final content for ${role}:`, content);

                    if (content.trim()) {
                        this.addMessage(role, content.trim());
                        console.log(`Added message: ${role} - ${content.trim()}`);
                    }
                } else {
                    console.log(`Skipping non-message item of type: ${item.type}`);
                }
            });
        } else {
            console.log('History is not an array or is null/undefined');
        }

        this.scrollToBottom();
    }

    addMessage(type, content) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type}`;

        const bubbleDiv = document.createElement('div');
        bubbleDiv.className = 'message-bubble';
        bubbleDiv.textContent = content;

        messageDiv.appendChild(bubbleDiv);
        this.messagesContent.appendChild(messageDiv);
        this.scrollToBottom();

        return messageDiv;
    }

    addRawEvent(event) {
        const eventDiv = document.createElement('div');
        eventDiv.className = 'event';

        const headerDiv = document.createElement('div');
        headerDiv.className = 'event-header';
        headerDiv.innerHTML = `
            <span>${event.type}</span>
            <span>▼</span>
        `;

        const contentDiv = document.createElement('div');
        contentDiv.className = 'event-content collapsed';
        contentDiv.textContent = JSON.stringify(event, null, 2);

        headerDiv.addEventListener('click', () => {
            const isCollapsed = contentDiv.classList.contains('collapsed');
            contentDiv.classList.toggle('collapsed');
            headerDiv.querySelector('span:last-child').textContent = isCollapsed ? '▲' : '▼';
        });

        eventDiv.appendChild(headerDiv);
        eventDiv.appendChild(contentDiv);
        this.eventsContent.appendChild(eventDiv);

        // Auto-scroll events pane
        this.eventsContent.scrollTop = this.eventsContent.scrollHeight;
    }

    addToolEvent(event) {
        const eventDiv = document.createElement('div');
        eventDiv.className = 'event';

        let title = '';
        let description = '';
        let eventClass = '';

        if (event.type === 'handoff') {
            title = `🔄 Handoff`;
            description = `From ${event.from} to ${event.to}`;
            eventClass = 'handoff';
        } else if (event.type === 'tool_start') {
            title = `🔧 Tool Started`;
            description = `Running ${event.tool}`;
            eventClass = 'tool';
        } else if (event.type === 'tool_end') {
            title = `✅ Tool Completed`;
            description = `${event.tool}: ${event.output || 'No output'}`;
            eventClass = 'tool';
        }

        eventDiv.innerHTML = `
            <div class="event-header ${eventClass}">
                <div>
                    <div style="font-weight: 600; margin-bottom: 2px;">${title}</div>
                    <div style="font-size: 0.8rem; opacity: 0.8;">${description}</div>
                </div>
                <span style="font-size: 0.7rem; opacity: 0.6;">${new Date().toLocaleTimeString()}</span>
            </div>
        `;

        this.toolsContent.appendChild(eventDiv);

        // Auto-scroll tools pane
        this.toolsContent.scrollTop = this.toolsContent.scrollHeight;
    }

    async playAudio(audioBase64) {
        try {
            if (!audioBase64 || audioBase64.length === 0) {
                console.warn('Received empty audio data, skipping playback');
                return;
            }

            // Add to queue
            this.audioQueue.push(audioBase64);

            // Start processing queue if not already playing
            if (!this.isPlayingAudio) {
                this.processAudioQueue();
            }

        } catch (error) {
            console.error('Failed to play audio:', error);
        }
    }

    async processAudioQueue() {
        if (this.isPlayingAudio || this.audioQueue.length === 0) {
            return;
        }

        this.isPlayingAudio = true;

        // Initialize audio context if needed
        if (!this.playbackAudioContext) {
            this.playbackAudioContext = new AudioContext({ sampleRate: 24000 });
        }

        while (this.audioQueue.length > 0) {
            const audioBase64 = this.audioQueue.shift();
            await this.playAudioChunk(audioBase64);
        }

        this.isPlayingAudio = false;
    }

    async playAudioChunk(audioBase64) {
        return new Promise((resolve, reject) => {
            try {
                // Decode base64 to ArrayBuffer
                const binaryString = atob(audioBase64);
                const bytes = new Uint8Array(binaryString.length);
                for (let i = 0; i < binaryString.length; i++) {
                    bytes[i] = binaryString.charCodeAt(i);
                }

                const int16Array = new Int16Array(bytes.buffer);

                if (int16Array.length === 0) {
                    console.warn('Audio chunk has no samples, skipping');
                    resolve();
                    return;
                }

                const float32Array = new Float32Array(int16Array.length);

                // Convert int16 to float32
                for (let i = 0; i < int16Array.length; i++) {
                    float32Array[i] = int16Array[i] / 32768.0;
                }

                const audioBuffer = this.playbackAudioContext.createBuffer(1, float32Array.length, 24000);
                audioBuffer.getChannelData(0).set(float32Array);

                const source = this.playbackAudioContext.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(this.playbackAudioContext.destination);

                // Store reference to current source
                this.currentAudioSource = source;

                source.onended = () => {
                    this.currentAudioSource = null;
                    resolve();
                };
                source.start();

            } catch (error) {
                console.error('Failed to play audio chunk:', error);
                reject(error);
            }
        });
    }

    stopAudioPlayback() {
        console.log('Stopping audio playback due to interruption');

        // Stop current audio source if playing
        if (this.currentAudioSource) {
            try {
                this.currentAudioSource.stop();
                this.currentAudioSource = null;
            } catch (error) {
                console.error('Error stopping audio source:', error);
            }
        }

        // Clear the audio queue
        this.audioQueue = [];

        // Reset playback state
        this.isPlayingAudio = false;

        console.log('Audio playback stopped and queue cleared');
    }

    scrollToBottom() {
        this.messagesContent.scrollTop = this.messagesContent.scrollHeight;
    }

    sendAskRequestPayload() {
        try {
            const jsonText = this.jsonEditor.value.trim();
            const payload = JSON.parse(jsonText);

            // Always refresh chat_session_id and product on send to avoid reusing sessions.
            const newId = this.generateUUID();
            payload.chat_session_id = newId;
            payload.product = this.agentSelect.value;
            this.jsonEditor.value = JSON.stringify(payload, null, 2);
            if (this.sessionIdDisplay) {
                this.sessionIdDisplay.textContent = newId;
            }
            this.ws.send(JSON.stringify({
                type: 'ask_request',
                data: payload
            }));
            console.log('Sent AskRequest payload:', payload);
            return true;
        } catch (error) {
            console.error('Failed to parse or send AskRequest JSON:', error);
            alert('Invalid JSON in the payload editor. Please fix the JSON and try again.');
            this.disconnect();
            return false;
        }
    }
}

// Initialize the demo when the page loads
document.addEventListener('DOMContentLoaded', () => {
    new RealtimeDemo();
});
