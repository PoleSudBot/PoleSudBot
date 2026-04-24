(function () {
  const page = window.__PROFILE_PAGE__ || {};
  const characterRanks = page.characterRanks || {};
  const themeColor = page.themeColor || "#33ccbb";
  const CHAR_NAMES = {
    1: "一歌",
    2: "咲希",
    3: "穗波",
    4: "志步",
    5: "实乃理",
    6: "遥",
    7: "爱莉",
    8: "雫",
    9: "心羽",
    10: "杏",
    11: "彰人",
    12: "冬弥",
    13: "司",
    14: "笑梦",
    15: "宁宁",
    16: "类",
    17: "奏",
    18: "真冬",
    19: "绘名",
    20: "瑞希",
    21: "Miku",
    22: "Rin",
    23: "Len",
    24: "Luka",
    25: "MEIKO",
    26: "KAITO",
  };
  const UNIT_DATA = {
    "总览": { color: "#888888", members: [] },
    "L/n": { color: "#4455dd", members: [1, 2, 3, 4] },
    MMJ: { color: "#88dd44", members: [5, 6, 7, 8] },
    VBS: { color: "#ee1166", members: [9, 10, 11, 12] },
    WxS: { color: "#ff9900", members: [13, 14, 15, 16] },
    "25时": { color: "#884499", members: [17, 18, 19, 20] },
    VS: { color: "#33ccbb", members: [21, 22, 23, 24, 25, 26] },
  };

  const chartCanvas = document.getElementById("unit-chart-canvas");
  const loadedMarker = document.querySelector(".unit-chart-loaded");
  if (!chartCanvas || !window.Chart || !window.ChartDataLabels) {
    if (loadedMarker) {
      loadedMarker.textContent = "chart-skip";
    }
    return;
  }

  window.Chart.register(window.ChartDataLabels);

  let currentUnit = "总览";
  let chartInstance = null;

  function rankFor(characterId) {
    return Number(characterRanks[characterId] || characterRanks[String(characterId)] || 1);
  }

  function chartData() {
    const labels = [];
    const values = [];
    const pointColors = [];
    let borderColor = themeColor;
    let bgColor = themeColor + "20";
    const isOverview = currentUnit === "总览";

    if (isOverview) {
      Object.keys(UNIT_DATA).slice(1).forEach((unit) => {
        const info = UNIT_DATA[unit];
        info.members.forEach((characterId) => {
          labels.push(CHAR_NAMES[characterId] || String(characterId));
          values.push(rankFor(characterId));
          pointColors.push(info.color);
        });
      });
    } else {
      const info = UNIT_DATA[currentUnit];
      if (!info) {
        return null;
      }
      borderColor = info.color;
      bgColor = info.color + "30";
      info.members.forEach((characterId) => {
        labels.push(CHAR_NAMES[characterId] || String(characterId));
        values.push(rankFor(characterId));
        pointColors.push(info.color);
      });
    }

    return {
      labels,
      values,
      pointColors,
      borderColor,
      bgColor,
      isOverview,
    };
  }

  function renderChart() {
    const data = chartData();
    if (!data) {
      return;
    }

    const maxValue = Math.max.apply(null, data.values.concat([10]));
    const suggestedMax = Math.ceil(maxValue / 10) * 10;
    const labelFontSize = data.isOverview ? 9 : 14;
    const dataLabelFontSize = data.isOverview ? 8 : 10;
    const dataLabelOffset = data.isOverview ? 0 : 4;

    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }

    chartInstance = new window.Chart(chartCanvas, {
      type: "radar",
      data: {
        labels: data.labels,
        datasets: [
          {
            data: data.values,
            backgroundColor: data.bgColor,
            borderColor: data.borderColor,
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: data.pointColors,
            pointBorderColor: "#fff",
            pointBorderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: false },
          datalabels: {
            display: true,
            color: "#333",
            font: { weight: "bold", size: dataLabelFontSize },
            align: function (context) {
              const value = context.dataset.data[context.dataIndex];
              const scaleMax = context.chart.scales.r.max || suggestedMax;
              return value > scaleMax * 0.8 ? "start" : "end";
            },
            anchor: "end",
            offset: dataLabelOffset,
            formatter: Math.round,
          },
        },
        layout: {
          padding: 10,
        },
        scales: {
          r: {
            beginAtZero: true,
            suggestedMax: suggestedMax,
            ticks: { display: false, stepSize: 10 },
            pointLabels: {
              display: true,
              padding: 5,
              font: { size: labelFontSize, weight: "600" },
              color: "#666",
            },
            grid: { color: "rgba(0,0,0,0.05)" },
            angleLines: { display: true, color: "rgba(0,0,0,0.2)" },
          },
        },
      },
    });

    if (loadedMarker) {
      loadedMarker.textContent = "chart-ready";
    }
  }

  document.querySelectorAll(".unit-tab").forEach((tab) => {
    tab.addEventListener("click", function () {
      currentUnit = tab.dataset.unit || "总览";
      document.querySelectorAll(".unit-tab").forEach((item) => {
        item.classList.toggle("active", item === tab);
      });
      renderChart();
    });
  });

  requestAnimationFrame(function () {
    renderChart();
    document.body.classList.add("profile-render-ready");
  });
})();
