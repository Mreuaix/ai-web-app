(function () {
  async function fetchSummary() {
    const r = await fetch('/api/bigscreen/summary');
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || '加载失败');
    return d;
  }

  function renderHot(hot) {
    const el = document.getElementById('hotList');
    if (!el) return;
    el.innerHTML = '';
    for (const item of hot) {
      const row = document.createElement('div');
      row.className = 'hotitem';
      row.innerHTML = `
        <div class="hotitem__title"><a href="${item.url}" target="_blank" rel="noopener noreferrer">${item.title}</a></div>
        <div class="hotitem__meta">
          <span>${(item.collected_at || '').slice(0, 19).replace('T',' ')}</span>
          <span class="badge badge--muted">${item.source}</span>
        </div>
      `;
      el.appendChild(row);
    }
  }

  async function ensureChinaMap(echarts) {
    if (echarts.getMap && echarts.getMap('china')) return;
    const url = 'https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json';
    const r = await fetch(url);
    const geojson = await r.json();
    echarts.registerMap('china', geojson);
  }

  function renderSourceChart(echarts, sources) {
    const el = document.getElementById('sourceChart');
    if (!el) return;
    const chart = echarts.init(el, null, { renderer: 'canvas' });
    chart.setOption({
      tooltip: { trigger: 'item' },
      series: [
        {
          type: 'pie',
          radius: ['35%', '70%'],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: 'rgba(6,10,18,0.9)', borderWidth: 2 },
          label: { color: 'rgba(255,255,255,0.75)' },
          data: sources || []
        }
      ]
    });
    window.addEventListener('resize', () => chart.resize());
  }

  function renderGlobe(echarts) {
    const el = document.getElementById('chinaMap');
    if (!el) return false;
    const chart = echarts.init(el, null, { renderer: 'canvas' });

    const beijing = [116.4074, 39.9042];
    const cnCities = [
      { name: '上海', coord: [121.4737, 31.2304] },
      { name: '广州', coord: [113.2644, 23.1291] },
      { name: '成都', coord: [104.0665, 30.5723] }
    ];
    const worldCities = [
      { name: '纽约', coord: [-74.0060, 40.7128] },
      { name: '洛杉矶', coord: [-118.2437, 34.0522] },
      { name: '多伦多', coord: [-79.3832, 43.6532] },
      { name: '墨西哥城', coord: [-99.1332, 19.4326] },
      { name: '圣保罗', coord: [-46.6333, -23.5505] },
      { name: '伦敦', coord: [-0.1276, 51.5074] },
      { name: '巴黎', coord: [2.3522, 48.8566] },
      { name: '柏林', coord: [13.4050, 52.5200] },
      { name: '莫斯科', coord: [37.6173, 55.7558] },
      { name: '伊斯坦布尔', coord: [28.9784, 41.0082] },
      { name: '迪拜', coord: [55.2708, 25.2048] },
      { name: '利雅得', coord: [46.6753, 24.7136] },
      { name: '开罗', coord: [31.2357, 30.0444] },
      { name: '拉各斯', coord: [3.3792, 6.5244] },
      { name: '约翰内斯堡', coord: [28.0473, -26.2041] },
      { name: '内罗毕', coord: [36.8219, -1.2921] },
      { name: '新德里', coord: [77.1025, 28.7041] },
      { name: '孟买', coord: [72.8777, 19.0760] },
      { name: '新加坡', coord: [103.8198, 1.3521] },
      { name: '雅加达', coord: [106.8456, -6.2088] },
      { name: '东京', coord: [139.6917, 35.6895] },
      { name: '首尔', coord: [126.9780, 37.5665] },
      { name: '悉尼', coord: [151.2093, -33.8688] }
    ];

    const points = [
      { name: '北京', value: [beijing[0], beijing[1], 0] },
      ...cnCities.map(c => ({ name: c.name, value: [c.coord[0], c.coord[1], 0] })),
      ...worldCities.map(c => ({ name: c.name, value: [c.coord[0], c.coord[1], 0] }))
    ];
    const cnLines = cnCities.map(c => ({ coords: [c.coord, beijing] }));
    const worldLines = worldCities.map(c => ({ coords: [c.coord, beijing] }));

    try {
      chart.setOption({
        tooltip: { show: false },
        globe: {
          baseTexture:
            'https://fastly.jsdelivr.net/gh/apache/echarts-website@asf-site/examples/data-gl/asset/world.topo.bathy.200401.jpg',
          shading: 'lambert',
          light: {
            main: { intensity: 1.1, shadow: false },
            ambient: { intensity: 0.35 }
          },
          viewControl: {
            autoRotate: true,
            autoRotateSpeed: 2.2,
            rotateSensitivity: 1,
            zoomSensitivity: 0.7,
            panSensitivity: 0
          }
        },
        series: [
          {
            type: 'lines3D',
            coordinateSystem: 'globe',
            blendMode: 'lighter',
            lineStyle: {
              width: 1.8,
              color: 'rgba(59,232,255,0.55)',
              opacity: 0.95
            },
            effect: {
              show: true,
              period: 2.2,
              trailWidth: 2.8,
              trailLength: 0.26,
              trailOpacity: 0.95,
              trailColor: '#3be8ff'
            },
            data: cnLines
          },
          {
            type: 'lines3D',
            coordinateSystem: 'globe',
            blendMode: 'lighter',
            lineStyle: {
              width: 1,
              color: 'rgba(59,232,255,0.28)',
              opacity: 0.85
            },
            effect: {
              show: true,
              period: 3.0,
              trailWidth: 2.2,
              trailLength: 0.22,
              trailOpacity: 0.9,
              trailColor: '#3be8ff'
            },
            data: worldLines
          },
          {
            type: 'scatter3D',
            coordinateSystem: 'globe',
            symbolSize: 4,
            itemStyle: { color: 'rgba(37,246,165,0.85)' },
            data: points.filter(p => p.name !== '北京')
          },
          {
            type: 'scatter3D',
            coordinateSystem: 'globe',
            symbolSize: 7,
            itemStyle: { color: '#25f6a5' },
            data: cnCities.map(c => ({ name: c.name, value: [c.coord[0], c.coord[1], 0] }))
          },
          {
            type: 'scatter3D',
            coordinateSystem: 'globe',
            symbolSize: 10,
            itemStyle: { color: '#3be8ff' },
            data: [{ name: '北京', value: [beijing[0], beijing[1], 0] }]
          }
        ]
      });
    } catch (e) {
      return false;
    }

    window.addEventListener('resize', () => chart.resize());
    return true;
  }

  function renderMap(echarts, sources) {
    const el = document.getElementById('chinaMap');
    if (!el) return;
    const chart = echarts.init(el, null, { renderer: 'canvas' });
    const data = [
      { name: '北京', value: 12 },
      { name: '上海', value: 10 },
      { name: '广东', value: 16 },
      { name: '浙江', value: 9 },
      { name: '四川', value: 7 },
      { name: '湖北', value: 6 },
      { name: '山东', value: 8 }
    ];

    chart.setOption({
      tooltip: { show: false },
      visualMap: {
        min: 0,
        max: 20,
        left: 18,
        bottom: 18,
        text: ['高', '低'],
        textStyle: { color: 'rgba(255,255,255,0.75)' },
        inRange: { color: ['#0b1a2b', '#1f7cff', '#3be8ff'] }
      },
      geo: {
        map: 'china',
        roam: false,
        selectedMode: false,
        zoom: 1.1,
        itemStyle: {
          areaColor: 'rgba(17, 38, 78, 0.55)',
          borderColor: 'rgba(59,232,255,0.25)'
        },
        emphasis: {
          itemStyle: {
            areaColor: 'rgba(59,232,255,0.18)'
          }
        },
        select: {
          itemStyle: {
            areaColor: 'rgba(17, 38, 78, 0.55)'
          }
        }
      },
      series: [
        {
          type: 'map',
          geoIndex: 0,
          selectedMode: false,
          emphasis: {
            itemStyle: {
              areaColor: 'rgba(17, 38, 78, 0.55)'
            }
          },
          select: {
            itemStyle: {
              areaColor: 'rgba(17, 38, 78, 0.55)'
            }
          },
          data
        },
        {
          type: 'effectScatter',
          coordinateSystem: 'geo',
          data: [
            { name: '北京', value: [116.40, 39.90, 12] },
            { name: '上海', value: [121.47, 31.23, 10] },
            { name: '广州', value: [113.27, 23.13, 16] },
            { name: '成都', value: [104.06, 30.67, 12] }
          ],
          symbolSize: (v) => Math.max(10, Math.min(22, v[2] + 6)),
          itemStyle: { color: '#25f6a5' },
          rippleEffect: { brushType: 'stroke' }
        }
      ]
    });
    window.addEventListener('resize', () => chart.resize());
  }

  async function boot() {
    const echarts = window.echarts;
    if (!echarts) return;
    const data = await fetchSummary();
    renderHot(data.hot || []);
    renderSourceChart(echarts, data.sources || []);
    const ok = renderGlobe(echarts);
    if (!ok) {
      await ensureChinaMap(echarts);
      renderMap(echarts, data.sources || []);
    }
  }

  boot();
  setInterval(() => {
    boot().catch(() => {});
  }, 15000);
})();
