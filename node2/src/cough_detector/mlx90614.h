// MLX90614 IR thermometer, register-level SMBus driver (E8 low-level driver).
//
// The sensor speaks SMBus (I2C at <= 100 kHz). Every RAM / EEPROM cell is a 16-bit word.
// Register map (RAM, opcode = 0x00 | address):
//   0x06  Ta     ambient (sensor die) temperature
//   0x07  Tobj1  object temperature, zone 1  <- forehead
//   0x08  Tobj2  zone 2 (dual-zone parts only)
//   value in kelvin = raw * 0.02, so celsius = raw * 0.02 - 273.15
//   bit 15 of the word = error flag (sensor reports the reading is invalid)
// EEPROM opcode = 0x20 | address (e.g. 0x2E = SMBus address), not used here.
//
// "Read Word" transaction, as on the bus:
//   S | addr<<1|W | command | Sr | addr<<1|R | LSB | MSB | PEC | P
//   Sr = repeated START: the master never releases the bus between writing the command
//   and reading the answer (Wire.endTransmission(false) does exactly this).
//
// PEC = CRC-8, polynomial x^8 + x^2 + x + 1 (0x07), init 0, computed over every byte on the
// bus before it: addr<<1|W, command, addr<<1|R, LSB, MSB. If our CRC != PEC the word was
// corrupted on the wire (loose cable, noise) and is rejected.
#ifndef MLX90614_H
#define MLX90614_H

#include <Arduino.h>
#include <Wire.h>

#define MLX_REG_TA 0x06
#define MLX_REG_TOBJ1 0x07

enum MlxStatus : uint8_t {
  MLX_OK = 0,
  MLX_NACK,        // no device answered (unplugged / wrong address / wiring)
  MLX_SHORT_READ,  // fewer than 3 bytes came back
  MLX_BAD_PEC,     // CRC mismatch: data corrupted on the bus
  MLX_ERR_FLAG,    // bit 15 set: sensor says the value is invalid
};

static const char *mlx_status_str(MlxStatus s) {
  switch (s) {
    case MLX_OK: return "ok";
    case MLX_NACK: return "NACK (not connected?)";
    case MLX_SHORT_READ: return "short read";
    case MLX_BAD_PEC: return "bad PEC (CRC)";
    case MLX_ERR_FLAG: return "error flag";
  }
  return "?";
}

static uint8_t mlx_addr = 0x5A;

// CRC-8 / SMBus PEC, bit by bit: shift in each byte, XOR the polynomial when the top bit falls out
static uint8_t mlx_crc8(const uint8_t *data, size_t len) {
  uint8_t crc = 0;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (int b = 0; b < 8; b++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
  }
  return crc;
}

static void mlx_init(int sda, int scl, uint8_t addr) {
  mlx_addr = addr;
  Wire.begin(sda, scl, 100000);  // SMBus max 100 kHz
  Wire.setTimeOut(50);           // ms; don't hang the task if the bus is stuck
}

// SMBus Read Word with PEC check
static MlxStatus mlx_read_word(uint8_t command, uint16_t *out) {
  Wire.beginTransmission(mlx_addr);
  Wire.write(command);
  if (Wire.endTransmission(false) != 0) return MLX_NACK;  // false = repeated START, keep the bus

  if (Wire.requestFrom(mlx_addr, (uint8_t)3) != 3) return MLX_SHORT_READ;
  uint8_t lsb = Wire.read(), msb = Wire.read(), pec = Wire.read();

  uint8_t frame[5] = {(uint8_t)(mlx_addr << 1), command, (uint8_t)((mlx_addr << 1) | 1), lsb, msb};
  if (mlx_crc8(frame, sizeof(frame)) != pec) return MLX_BAD_PEC;

  uint16_t word = ((uint16_t)msb << 8) | lsb;
  if (word & 0x8000) return MLX_ERR_FLAG;
  *out = word;
  return MLX_OK;
}

// temperature register -> celsius
static MlxStatus mlx_read_c(uint8_t reg, float *celsius) {
  uint16_t raw;
  MlxStatus s = mlx_read_word(reg, &raw);
  if (s == MLX_OK) *celsius = raw * 0.02f - 273.15f;
  return s;
}

#endif
