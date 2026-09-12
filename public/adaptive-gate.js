class SwarlinkAdaptiveGate extends AudioWorkletProcessor {
  constructor() {
    super();
    this.envelope = 0;
    this.floor = 0.0015;
    this.gain = 1;
  }

  process(inputs, outputs) {
    const input = inputs[0];
    const output = outputs[0];
    if (!input?.length || !output?.length) return true;
    for (let channel = 0; channel < output.length; channel += 1) {
      const source = input[Math.min(channel, input.length - 1)];
      const destination = output[channel];
      if (!source) { destination.fill(0); continue; }
      for (let index = 0; index < destination.length; index += 1) {
        const sample = source[index] || 0;
        const absolute = Math.abs(sample);
        this.envelope = absolute > this.envelope ? this.envelope * 0.82 + absolute * 0.18 : this.envelope * 0.992 + absolute * 0.008;
        if (this.envelope < this.floor * 1.35) this.floor = this.floor * 0.998 + this.envelope * 0.002;
        const threshold = Math.max(0.0012, this.floor * 2.8);
        const ratio = Math.min(1, this.envelope / threshold);
        const target = this.envelope >= threshold ? 1 : 0.08 + 0.92 * Math.pow(ratio, 2.2);
        this.gain += (target - this.gain) * (target > this.gain ? 0.06 : 0.012);
        destination[index] = sample * this.gain;
      }
    }
    return true;
  }
}

registerProcessor("swarlink-adaptive-gate", SwarlinkAdaptiveGate);
