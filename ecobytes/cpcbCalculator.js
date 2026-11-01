/**
 * EcoBytes - CPCB NAQI (National Air Quality Index) Calculator (JavaScript)
 * Implements the official Indian Central Pollution Control Board standard:
 * Ip = [(I_HI - I_LO) / (B_HI - B_LO)] * (Cp - B_LO) + I_LO
 */

const BREAKPOINTS = {
    pm25: [
        [0.0, 30.0, 0, 50],
        [30.0, 60.0, 51, 100],
        [60.0, 90.0, 101, 200],
        [90.0, 120.0, 201, 300],
        [120.0, 250.0, 301, 400],
        [250.0, 500.0, 401, 500],
    ],
    pm10: [
        [0.0, 50.0, 0, 50],
        [50.0, 100.0, 51, 100],
        [100.0, 250.0, 101, 200],
        [250.0, 350.0, 201, 300],
        [350.0, 430.0, 301, 400],
        [430.0, 600.0, 401, 500],
    ],
    no2: [
        [0.0, 40.0, 0, 50],
        [40.0, 80.0, 51, 100],
        [80.0, 180.0, 101, 200],
        [180.0, 280.0, 201, 300],
        [280.0, 400.0, 301, 400],
        [400.0, 600.0, 401, 500],
    ],
    o3: [
        [0.0, 50.0, 0, 50],
        [50.0, 100.0, 51, 100],
        [100.0, 168.0, 101, 200],
        [168.0, 208.0, 201, 300],
        [208.0, 748.0, 301, 400],
        [748.0, 1000.0, 401, 500],
    ],
    so2: [
        [0.0, 40.0, 0, 50],
        [40.0, 80.0, 51, 100],
        [80.0, 380.0, 101, 200],
        [380.0, 800.0, 201, 300],
        [800.0, 1600.0, 301, 400],
        [1600.0, 2000.0, 401, 500],
    ],
    co: [
        [0.0, 1.0, 0, 50],
        [1.0, 2.0, 51, 100],
        [2.0, 10.0, 101, 200],
        [10.0, 17.0, 201, 300],
        [17.0, 34.0, 301, 400],
        [34.0, 50.0, 401, 500],
    ]
};

const CATEGORIES = [
    [0, 50, "Good", "#00B050", "Minimal health impact. Air quality is considered clean and healthy."],
    [51, 100, "Satisfactory", "#92D050", "Minor breathing discomfort to sensitive people."],
    [101, 200, "Moderate", "#E5A93C", "Breathing discomfort to people with asthma, lungs, and heart diseases."],
    [201, 300, "Poor", "#FF7E00", "Breathing discomfort to most people on prolonged exposure."],
    [301, 400, "Very Poor", "#FF0000", "Respiratory illness on prolonged exposure. Significant risk to cardiac/respiratory patients."],
    [401, 500, "Severe", "#800000", "Affects healthy people and seriously impacts those with existing respiratory or cardiac diseases."]
];

function calculateSubIndex(pollutant, concentration) {
    if (concentration === null || concentration === undefined || concentration < 0) {
        return 0;
    }
    const pol = String(pollutant).toLowerCase();
    const table = BREAKPOINTS[pol];
    if (!table) return 0;

    for (let i = 0; i < table.length; i++) {
        const [b_lo, b_hi, i_lo, i_hi] = table[i];
        if (concentration >= b_lo && concentration <= b_hi) {
            const sub = ((i_hi - i_lo) / (b_hi - b_lo)) * (concentration - b_lo) + i_lo;
            return Math.round(sub);
        }
    }

    if (concentration > table[table.length - 1][1]) {
        return 500;
    }
    return 0;
}

function getCategoryInfo(aqi) {
    if (aqi === null || aqi === undefined || aqi < 0) {
        return { category: "Good", color: "#00B050", advisory: CATEGORIES[0][4] };
    }
    for (let i = 0; i < CATEGORIES.length; i++) {
        const [lo, hi, cat, color, advisory] = CATEGORIES[i];
        if (aqi >= lo && aqi <= hi) {
            return { category: cat, color, advisory };
        }
    }
    return { category: "Severe", color: "#800000", advisory: CATEGORIES[CATEGORIES.length - 1][4] };
}

function computeCpcbAqi(pollutants) {
    if (!pollutants || typeof pollutants !== 'object') {
        return {
            aqi: 50,
            category: "Good",
            color: "#00B050",
            advisory: CATEGORIES[0][4],
            dominant_pollutant: "PM2.5",
            sub_indices: {}
        };
    }

    const subIndices = {};
    let dominantPollutant = "PM2.5";
    let maxIndex = -1;

    for (const [pol, val] of Object.entries(pollutants)) {
        if (val !== null && val !== undefined && !isNaN(val)) {
            const sub = calculateSubIndex(pol, Number(val));
            subIndices[pol] = sub;
            if (sub > maxIndex) {
                maxIndex = sub;
                dominantPollutant = pol;
            }
        }
    }

    const finalAqi = maxIndex >= 0 ? maxIndex : 50;
    const catInfo = getCategoryInfo(finalAqi);

    return {
        aqi: finalAqi,
        category: catInfo.category,
        color: catInfo.color,
        advisory: catInfo.advisory,
        dominant_pollutant: dominantPollutant.toUpperCase(),
        sub_indices: subIndices
    };
}

function calculateCpcbAqi(pollutants) {
    return computeCpcbAqi(pollutants);
}

function invertAqiToConcentration(pollutant, aqi) {
    if (aqi === null || aqi === undefined || aqi <= 0) {
        return 0.0;
    }
    const pol = String(pollutant).toLowerCase();
    const table = BREAKPOINTS[pol];
    if (!table) return 0.0;

    for (let i = 0; i < table.length; i++) {
        const [b_lo, b_hi, i_lo, i_hi] = table[i];
        if (aqi >= i_lo && aqi <= i_hi) {
            const cp = b_lo + ((aqi - i_lo) / (i_hi - i_lo)) * (b_hi - b_lo);
            return Math.round(cp * 100) / 100;
        }
    }
    if (aqi > table[table.length - 1][3]) {
        return Number(table[table.length - 1][1]);
    }
    return 0.0;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        BREAKPOINTS,
        CATEGORIES,
        calculateSubIndex,
        invertAqiToConcentration,
        getCategoryInfo,
        computeCpcbAqi,
        calculateCpcbAqi
    };
}

