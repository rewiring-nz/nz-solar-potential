// Written by tools/fetch_regional_prices.mjs from the Electricity Authority's
// regional power prices. Do not edit by hand: run the tool.
const REGIONAL_PRICES = {
 "source": "https://www.ea.govt.nz/data-and-insights/datasets/retail/regional-power-prices/",
 "csv": "https://emidatasets.blob.core.windows.net/publicdata/Datasets/Retail/RegionalPowerPrices/20260531_AveragePowerUseAndCosts.csv",
 "load_type": "Standard user",
 "peak_ratio": 1.3077,
 "peak_hours": [
  [
   7,
   10
  ],
  [
   18,
   21
  ]
 ],
 "regions": {
  "1": {
   "name": "Bay of Islands (Top Energy)",
   "month": "2026-05-31",
   "daily": 3.5641,
   "variable_c": 39.25,
   "peak_share": 0.2915,
   "peak_c": 47.1,
   "offpeak_c": 36.02
  },
  "2": {
   "name": "Whangārei and Kaipara (Northpower)",
   "month": "2026-05-31",
   "daily": 3.4553,
   "variable_c": 30.56,
   "peak_share": 0.2915,
   "peak_c": 36.67,
   "offpeak_c": 28.04
  },
  "3": {
   "name": "Waitemata (Vector)",
   "month": "2026-05-31",
   "daily": 3.0329,
   "variable_c": 27.05,
   "peak_share": 0.2915,
   "peak_c": 32.46,
   "offpeak_c": 24.82
  },
  "4": {
   "name": "Auckland (Vector)",
   "month": "2026-05-31",
   "daily": 3.0828,
   "variable_c": 26.51,
   "peak_share": 0.2915,
   "peak_c": 31.81,
   "offpeak_c": 24.33
  },
  "5": {
   "name": "Counties (Counties Power)",
   "month": "2026-05-31",
   "daily": 3.1664,
   "variable_c": 33.35,
   "peak_share": 0.2915,
   "peak_c": 40.02,
   "offpeak_c": 30.6
  },
  "6": {
   "name": "Thames Valley (Powerco)",
   "month": "2026-05-31",
   "daily": 2.7213,
   "variable_c": 32.6,
   "peak_share": 0.2915,
   "peak_c": 39.12,
   "offpeak_c": 29.92
  },
  "7": {
   "name": "Waikato (WEL Networks)",
   "month": "2026-05-31",
   "daily": 3.1317,
   "variable_c": 26.51,
   "peak_share": 0.2915,
   "peak_c": 31.81,
   "offpeak_c": 24.33
  },
  "8": {
   "name": "Waipa (Waipa Networks)",
   "month": "2026-05-31",
   "daily": 1.9802,
   "variable_c": 36.81,
   "peak_share": 0.2915,
   "peak_c": 44.17,
   "offpeak_c": 33.78
  },
  "9": {
   "name": "King Country (The Lines Company)",
   "month": "2026-05-31",
   "daily": 3.7219,
   "variable_c": 32.88,
   "peak_share": 0.2915,
   "peak_c": 39.46,
   "offpeak_c": 30.17
  },
  "10": {
   "name": "Tauranga (Powerco)",
   "month": "2026-05-31",
   "daily": 2.6827,
   "variable_c": 34.43,
   "peak_share": 0.2915,
   "peak_c": 41.32,
   "offpeak_c": 31.6
  },
  "11": {
   "name": "Rotorua (Unison Networks)",
   "month": "2026-05-31",
   "daily": 2.7064,
   "variable_c": 32.9,
   "peak_share": 0.2915,
   "peak_c": 39.48,
   "offpeak_c": 30.19
  },
  "12": {
   "name": "Eastern Bay of Plenty (Horizon Energy)",
   "month": "2026-05-31",
   "daily": 3.676,
   "variable_c": 29.85,
   "peak_share": 0.2915,
   "peak_c": 35.82,
   "offpeak_c": 27.39
  },
  "13": {
   "name": "Taupō (Unison Networks)",
   "month": "2026-05-31",
   "daily": 2.723,
   "variable_c": 32.8,
   "peak_share": 0.2915,
   "peak_c": 39.36,
   "offpeak_c": 30.1
  },
  "14": {
   "name": "Tairāwhiti and Wairoa (Firstlight Network)",
   "month": "2026-05-31",
   "daily": 3.9982,
   "variable_c": 31.32,
   "peak_share": 0.2915,
   "peak_c": 37.59,
   "offpeak_c": 28.74
  },
  "15": {
   "name": "Hawke's Bay (Unison Networks)",
   "month": "2026-05-31",
   "daily": 2.7067,
   "variable_c": 31.38,
   "peak_share": 0.2915,
   "peak_c": 37.66,
   "offpeak_c": 28.8
  },
  "16": {
   "name": "Central Hawke's Bay (Centralines)",
   "month": "2026-05-31",
   "daily": 3.2833,
   "variable_c": 37.46,
   "peak_share": 0.2915,
   "peak_c": 44.95,
   "offpeak_c": 34.38
  },
  "17": {
   "name": "Southern Hawke's Bay (Scanpower)",
   "month": "2026-05-31",
   "daily": 2.0083,
   "variable_c": 36.84,
   "peak_share": 0.2915,
   "peak_c": 44.21,
   "offpeak_c": 33.81
  },
  "18": {
   "name": "Wairarapa (Powerco)",
   "month": "2026-05-31",
   "daily": 2.6492,
   "variable_c": 37,
   "peak_share": 0.2915,
   "peak_c": 44.4,
   "offpeak_c": 33.95
  },
  "19": {
   "name": "Taranaki (Powerco)",
   "month": "2026-05-31",
   "daily": 2.5141,
   "variable_c": 33.02,
   "peak_share": 0.2915,
   "peak_c": 39.63,
   "offpeak_c": 30.3
  },
  "20": {
   "name": "Whanganui (Powerco)",
   "month": "2026-05-31",
   "daily": 2.4155,
   "variable_c": 35.1,
   "peak_share": 0.2915,
   "peak_c": 42.12,
   "offpeak_c": 32.21
  },
  "21": {
   "name": "Manawatū (Powerco)",
   "month": "2026-05-31",
   "daily": 2.3567,
   "variable_c": 32.56,
   "peak_share": 0.2915,
   "peak_c": 39.07,
   "offpeak_c": 29.88
  },
  "22": {
   "name": "Kāpiti and Horowhenua (Electra)",
   "month": "2026-05-31",
   "daily": 2.2777,
   "variable_c": 35.23,
   "peak_share": 0.2915,
   "peak_c": 42.28,
   "offpeak_c": 32.33
  },
  "23": {
   "name": "Wellington (Wellington Electricity)",
   "month": "2026-05-31",
   "daily": 2.678,
   "variable_c": 26.29,
   "peak_share": 0.2915,
   "peak_c": 31.55,
   "offpeak_c": 24.13
  },
  "25": {
   "name": "Tasman (Network Tasman)",
   "month": "2026-05-31",
   "daily": 2.5902,
   "variable_c": 30.18,
   "peak_share": 0.2915,
   "peak_c": 36.22,
   "offpeak_c": 27.7
  },
  "26": {
   "name": "Marlborough (Marlborough Lines)",
   "month": "2026-05-31",
   "daily": 4.2884,
   "variable_c": 31.16,
   "peak_share": 0.2915,
   "peak_c": 37.39,
   "offpeak_c": 28.6
  },
  "27": {
   "name": "Buller (Buller Electricity)",
   "month": "2026-05-31",
   "daily": 3.3336,
   "variable_c": 36.22,
   "peak_share": 0.2915,
   "peak_c": 43.47,
   "offpeak_c": 33.24
  },
  "28": {
   "name": "West Coast (Westpower)",
   "month": "2026-05-31",
   "daily": 2.2412,
   "variable_c": 40.22,
   "peak_share": 0.2915,
   "peak_c": 48.27,
   "offpeak_c": 36.91
  },
  "29": {
   "name": "North Canterbury (MainPower NZ)",
   "month": "2026-05-31",
   "daily": 3.837,
   "variable_c": 28.12,
   "peak_share": 0.2915,
   "peak_c": 33.75,
   "offpeak_c": 25.81
  },
  "30": {
   "name": "Central Canterbury (Orion New Zealand)",
   "month": "2026-05-31",
   "daily": 2.0813,
   "variable_c": 27.68,
   "peak_share": 0.2915,
   "peak_c": 33.22,
   "offpeak_c": 25.4
  },
  "31": {
   "name": "Ashburton (Electricity Ashburton)",
   "month": "2026-05-31",
   "daily": 1.9568,
   "variable_c": 30.64,
   "peak_share": 0.2915,
   "peak_c": 36.77,
   "offpeak_c": 28.12
  },
  "32": {
   "name": "South Canterbury (Alpine Energy)",
   "month": "2026-05-31",
   "daily": 3.7548,
   "variable_c": 27.34,
   "peak_share": 0.2915,
   "peak_c": 32.81,
   "offpeak_c": 25.09
  },
  "33": {
   "name": "Waitaki (Network Waitaki)",
   "month": "2026-05-31",
   "daily": 4.2605,
   "variable_c": 24.73,
   "peak_share": 0.2915,
   "peak_c": 29.68,
   "offpeak_c": 22.69
  },
  "34": {
   "name": "Queenstown (Aurora Energy)",
   "month": "2026-05-31",
   "daily": 2.0224,
   "variable_c": 33.55,
   "peak_share": 0.2934,
   "peak_c": 40.24,
   "offpeak_c": 30.77
  },
  "35": {
   "name": "Central Otago (Aurora Energy)",
   "month": "2026-05-31",
   "daily": 1.9908,
   "variable_c": 40.69,
   "peak_share": 0.2915,
   "peak_c": 48.83,
   "offpeak_c": 37.34
  },
  "36": {
   "name": "Otago (OtagoNet)",
   "month": "2026-05-31",
   "daily": 3.24,
   "variable_c": 36.97,
   "peak_share": 0.2915,
   "peak_c": 44.37,
   "offpeak_c": 33.93
  },
  "37": {
   "name": "Dunedin (Aurora Energy)",
   "month": "2026-05-31",
   "daily": 2.0561,
   "variable_c": 31.63,
   "peak_share": 0.2915,
   "peak_c": 37.96,
   "offpeak_c": 29.03
  },
  "38": {
   "name": "Southland (The Power Company)",
   "month": "2026-05-31",
   "daily": 3.3807,
   "variable_c": 31.28,
   "peak_share": 0.2915,
   "peak_c": 37.54,
   "offpeak_c": 28.71
  },
  "39": {
   "name": "Invercargill (Electricity Invercargill)",
   "month": "2026-05-31",
   "daily": 2.7241,
   "variable_c": 27.24,
   "peak_share": 0.2915,
   "peak_c": 32.69,
   "offpeak_c": 25
  },
  "40": {
   "name": "Frankton (Lakelands)",
   "month": "2026-05-31",
   "daily": 2.284,
   "variable_c": 33.18,
   "peak_share": 0.2915,
   "peak_c": 39.82,
   "offpeak_c": 30.45
  }
 }
};
if (typeof module !== "undefined" && module.exports) module.exports = { REGIONAL_PRICES };
