// Uplink decoder for the cellar-flood-sentinel 8-byte payload (port 1).
// Works as a LoRaWAN network-server payload formatter: decodeUplink({bytes, fPort}).
// Layout: see cellar_flood/payload.py.
function decodeUplink(input) {
  var b = input.bytes;
  var errors = [];
  var warnings = [];
  if (input.fPort !== undefined && input.fPort !== 1) {
    return { data: {}, warnings: warnings, errors: ["unexpected fPort " + input.fPort] };
  }
  if (!b || b.length !== 8) {
    return { data: {}, warnings: warnings, errors: ["payload must be 8 bytes, got " + (b ? b.length : 0)] };
  }
  var f = b[0];
  if (f & 0x20) {
    return { data: {}, warnings: warnings, errors: ["reserved flag bit set"] };
  }
  var msgType = f >> 6;
  if (msgType === 3) {
    return { data: {}, warnings: warnings, errors: ["unknown message type 3"] };
  }
  var level = (b[1] << 8) | b[2];
  var rate = b[3] > 127 ? b[3] - 256 : b[3];
  var data = {
    wet: !!(f & 1),
    pump_running: !!(f & 2),
    selftest_ok: !!(f & 4),
    low_battery: !!(f & 8),
    sensor_fault: !!(f & 16),
    msg_type: ["periodic", "alarm", "heartbeat"][msgType],
    level_mm: level === 0xffff ? null : level,
    rate_mm_min: rate * 0.5,
    pump_current_a: Math.round(b[4]) / 10,
    pump_run_s: b[5] * 2,
    pump_starts_1h: b[6],
    battery_v: Math.round(200 + b[7]) / 100,
  };
  if (data.low_battery) { warnings.push("low battery"); }
  if (data.sensor_fault) { warnings.push("level sensor fault"); }
  return { data: data, warnings: warnings, errors: errors };
}

if (typeof module !== "undefined") { module.exports = { decodeUplink: decodeUplink }; }
