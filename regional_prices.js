// Written by tools/fetch_regional_prices.mjs from the Electricity Authority's
// regional power prices. Do not edit by hand: run the tool.
const REGIONAL_PRICES = {
 "source": "https://www.ea.govt.nz/data-and-insights/datasets/retail/regional-power-prices/",
 "csv": "https://emidatasets.blob.core.windows.net/publicdata/Datasets/Retail/RegionalPowerPrices/20260531_AveragePowerUseAndCosts.csv",
 "load_type": "Standard user",
 "peak_ratio": 2,
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
   "daily": 3.56,
   "variable_c": 39.25,
   "peak_share": 0.3654,
   "peak_c": 57,
   "offpeak_c": 29
  },
  "2": {
   "name": "Whangārei and Kaipara (Northpower)",
   "month": "2026-05-31",
   "daily": 3.46,
   "variable_c": 30.56,
   "peak_share": 0.3654,
   "peak_c": 45,
   "offpeak_c": 22
  },
  "3": {
   "name": "Waitemata (Vector)",
   "month": "2026-05-31",
   "daily": 3.03,
   "variable_c": 27.05,
   "peak_share": 0.3654,
   "peak_c": 40,
   "offpeak_c": 20
  },
  "4": {
   "name": "Auckland (Vector)",
   "month": "2026-05-31",
   "daily": 3.08,
   "variable_c": 26.51,
   "peak_share": 0.3654,
   "peak_c": 39,
   "offpeak_c": 19
  },
  "5": {
   "name": "Counties (Counties Power)",
   "month": "2026-05-31",
   "daily": 3.17,
   "variable_c": 33.35,
   "peak_share": 0.3654,
   "peak_c": 49,
   "offpeak_c": 24
  },
  "6": {
   "name": "Thames Valley (Powerco)",
   "month": "2026-05-31",
   "daily": 2.72,
   "variable_c": 32.6,
   "peak_share": 0.3654,
   "peak_c": 48,
   "offpeak_c": 24
  },
  "7": {
   "name": "Waikato (WEL Networks)",
   "month": "2026-05-31",
   "daily": 3.13,
   "variable_c": 26.51,
   "peak_share": 0.3654,
   "peak_c": 39,
   "offpeak_c": 19
  },
  "8": {
   "name": "Waipa (Waipa Networks)",
   "month": "2026-05-31",
   "daily": 1.98,
   "variable_c": 36.81,
   "peak_share": 0.3654,
   "peak_c": 54,
   "offpeak_c": 27
  },
  "9": {
   "name": "King Country (The Lines Company)",
   "month": "2026-05-31",
   "daily": 3.72,
   "variable_c": 32.88,
   "peak_share": 0.3654,
   "peak_c": 48,
   "offpeak_c": 24
  },
  "10": {
   "name": "Tauranga (Powerco)",
   "month": "2026-05-31",
   "daily": 2.68,
   "variable_c": 34.43,
   "peak_share": 0.3654,
   "peak_c": 50,
   "offpeak_c": 25
  },
  "11": {
   "name": "Rotorua (Unison Networks)",
   "month": "2026-05-31",
   "daily": 2.71,
   "variable_c": 32.9,
   "peak_share": 0.3654,
   "peak_c": 48,
   "offpeak_c": 24
  },
  "12": {
   "name": "Eastern Bay of Plenty (Horizon Energy)",
   "month": "2026-05-31",
   "daily": 3.68,
   "variable_c": 29.85,
   "peak_share": 0.3654,
   "peak_c": 44,
   "offpeak_c": 22
  },
  "13": {
   "name": "Taupō (Unison Networks)",
   "month": "2026-05-31",
   "daily": 2.72,
   "variable_c": 32.8,
   "peak_share": 0.3654,
   "peak_c": 48,
   "offpeak_c": 24
  },
  "14": {
   "name": "Tairāwhiti and Wairoa (Firstlight Network)",
   "month": "2026-05-31",
   "daily": 4,
   "variable_c": 31.32,
   "peak_share": 0.3654,
   "peak_c": 46,
   "offpeak_c": 23
  },
  "15": {
   "name": "Hawke's Bay (Unison Networks)",
   "month": "2026-05-31",
   "daily": 2.71,
   "variable_c": 31.38,
   "peak_share": 0.3654,
   "peak_c": 46,
   "offpeak_c": 23
  },
  "16": {
   "name": "Central Hawke's Bay (Centralines)",
   "month": "2026-05-31",
   "daily": 3.28,
   "variable_c": 37.46,
   "peak_share": 0.3654,
   "peak_c": 55,
   "offpeak_c": 27
  },
  "17": {
   "name": "Southern Hawke's Bay (Scanpower)",
   "month": "2026-05-31",
   "daily": 2.01,
   "variable_c": 36.84,
   "peak_share": 0.3654,
   "peak_c": 54,
   "offpeak_c": 27
  },
  "18": {
   "name": "Wairarapa (Powerco)",
   "month": "2026-05-31",
   "daily": 2.65,
   "variable_c": 37,
   "peak_share": 0.3654,
   "peak_c": 54,
   "offpeak_c": 27
  },
  "19": {
   "name": "Taranaki (Powerco)",
   "month": "2026-05-31",
   "daily": 2.51,
   "variable_c": 33.02,
   "peak_share": 0.3654,
   "peak_c": 48,
   "offpeak_c": 24
  },
  "20": {
   "name": "Whanganui (Powerco)",
   "month": "2026-05-31",
   "daily": 2.42,
   "variable_c": 35.1,
   "peak_share": 0.3654,
   "peak_c": 51,
   "offpeak_c": 26
  },
  "21": {
   "name": "Manawatū (Powerco)",
   "month": "2026-05-31",
   "daily": 2.36,
   "variable_c": 32.56,
   "peak_share": 0.3654,
   "peak_c": 48,
   "offpeak_c": 24
  },
  "22": {
   "name": "Kāpiti and Horowhenua (Electra)",
   "month": "2026-05-31",
   "daily": 2.28,
   "variable_c": 35.23,
   "peak_share": 0.3654,
   "peak_c": 52,
   "offpeak_c": 26
  },
  "23": {
   "name": "Wellington (Wellington Electricity)",
   "month": "2026-05-31",
   "daily": 2.68,
   "variable_c": 26.29,
   "peak_share": 0.3654,
   "peak_c": 39,
   "offpeak_c": 19
  },
  "25": {
   "name": "Tasman (Network Tasman)",
   "month": "2026-05-31",
   "daily": 2.59,
   "variable_c": 30.18,
   "peak_share": 0.3654,
   "peak_c": 44,
   "offpeak_c": 22
  },
  "26": {
   "name": "Marlborough (Marlborough Lines)",
   "month": "2026-05-31",
   "daily": 4.29,
   "variable_c": 31.16,
   "peak_share": 0.3654,
   "peak_c": 46,
   "offpeak_c": 23
  },
  "27": {
   "name": "Buller (Buller Electricity)",
   "month": "2026-05-31",
   "daily": 3.33,
   "variable_c": 36.22,
   "peak_share": 0.3654,
   "peak_c": 53,
   "offpeak_c": 27
  },
  "28": {
   "name": "West Coast (Westpower)",
   "month": "2026-05-31",
   "daily": 2.24,
   "variable_c": 40.22,
   "peak_share": 0.3654,
   "peak_c": 59,
   "offpeak_c": 29
  },
  "29": {
   "name": "North Canterbury (MainPower NZ)",
   "month": "2026-05-31",
   "daily": 3.84,
   "variable_c": 28.12,
   "peak_share": 0.3654,
   "peak_c": 41,
   "offpeak_c": 21
  },
  "30": {
   "name": "Central Canterbury (Orion New Zealand)",
   "month": "2026-05-31",
   "daily": 2.08,
   "variable_c": 27.68,
   "peak_share": 0.3654,
   "peak_c": 41,
   "offpeak_c": 20
  },
  "31": {
   "name": "Ashburton (Electricity Ashburton)",
   "month": "2026-05-31",
   "daily": 1.96,
   "variable_c": 30.64,
   "peak_share": 0.3654,
   "peak_c": 45,
   "offpeak_c": 22
  },
  "32": {
   "name": "South Canterbury (Alpine Energy)",
   "month": "2026-05-31",
   "daily": 3.75,
   "variable_c": 27.34,
   "peak_share": 0.3654,
   "peak_c": 40,
   "offpeak_c": 20
  },
  "33": {
   "name": "Waitaki (Network Waitaki)",
   "month": "2026-05-31",
   "daily": 4.26,
   "variable_c": 24.73,
   "peak_share": 0.3654,
   "peak_c": 36,
   "offpeak_c": 18
  },
  "34": {
   "name": "Queenstown (Aurora Energy)",
   "month": "2026-05-31",
   "daily": 2.02,
   "variable_c": 33.55,
   "peak_share": 0.3688,
   "peak_c": 49,
   "offpeak_c": 25
  },
  "35": {
   "name": "Central Otago (Aurora Energy)",
   "month": "2026-05-31",
   "daily": 1.99,
   "variable_c": 40.69,
   "peak_share": 0.3688,
   "peak_c": 59,
   "offpeak_c": 30
  },
  "36": {
   "name": "Otago (OtagoNet)",
   "month": "2026-05-31",
   "daily": 3.24,
   "variable_c": 36.97,
   "peak_share": 0.3654,
   "peak_c": 54,
   "offpeak_c": 27
  },
  "37": {
   "name": "Dunedin (Aurora Energy)",
   "month": "2026-05-31",
   "daily": 2.06,
   "variable_c": 31.63,
   "peak_share": 0.3654,
   "peak_c": 46,
   "offpeak_c": 23
  },
  "38": {
   "name": "Southland (The Power Company)",
   "month": "2026-05-31",
   "daily": 3.38,
   "variable_c": 31.28,
   "peak_share": 0.3688,
   "peak_c": 46,
   "offpeak_c": 23
  },
  "39": {
   "name": "Invercargill (Electricity Invercargill)",
   "month": "2026-05-31",
   "daily": 2.72,
   "variable_c": 27.24,
   "peak_share": 0.3654,
   "peak_c": 40,
   "offpeak_c": 20
  },
  "40": {
   "name": "Frankton (Lakelands)",
   "month": "2026-05-31",
   "daily": 2.28,
   "variable_c": 33.18,
   "peak_share": 0.3654,
   "peak_c": 49,
   "offpeak_c": 24
  }
 }
};
if (typeof module !== "undefined" && module.exports) module.exports = { REGIONAL_PRICES };
