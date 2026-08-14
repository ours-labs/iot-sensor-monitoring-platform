document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('tableSearch');
    const table = document.querySelector('table');

    // 1. リアルタイム検索機能 (テーブル用)
    if (searchInput && table) {
        searchInput.addEventListener('input', () => {
            const filter = searchInput.value.toLowerCase();
            const rows = table.querySelectorAll('tbody tr');

            rows.forEach(row => {
                const text = row.textContent.toLowerCase();
                row.style.display = text.includes(filter) ? '' : 'none';
            });
        });

        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                searchInput.blur();
            }
        });
    }

    // 2. テーブルソート機能
    if (table) {
        const headers = table.querySelectorAll('th');
        headers.forEach((header, index) => {
            header.style.cursor = 'pointer';
            header.addEventListener('click', () => {
                const rows = Array.from(table.querySelectorAll('tbody tr'));
                const isAsc = header.classList.toggle('asc');

                rows.sort((a, b) => {
                    const aVal = a.cells[index].innerText;
                    const bVal = b.cells[index].innerText;
                    return isAsc ? aVal.localeCompare(bVal, undefined, {numeric: true})
                                 : bVal.localeCompare(aVal, undefined, {numeric: true});
                });

                const tbody = table.querySelector('tbody');
                rows.forEach(row => tbody.appendChild(row));
            });
        });
    }

    // 3. 上下の横スクロールバーを同じ位置へ同期
    const tableScrollTop = document.getElementById('tableScrollTop');
    const tableScrollBottom = document.getElementById('tableScrollBottom');
    const tableScrollSpacer = document.getElementById('tableScrollSpacer');

    if (table && tableScrollTop && tableScrollBottom && tableScrollSpacer) {
        let syncingScroll = false;

        const updateScrollWidth = () => {
            tableScrollSpacer.style.width = `${table.scrollWidth}px`;
            tableScrollTop.hidden = table.scrollWidth <= tableScrollBottom.clientWidth;
        };

        const synchronizeScroll = (source, target) => {
            if (syncingScroll) {
                return;
            }
            syncingScroll = true;
            target.scrollLeft = source.scrollLeft;
            window.requestAnimationFrame(() => {
                syncingScroll = false;
            });
        };

        tableScrollTop.addEventListener('scroll', () => {
            synchronizeScroll(tableScrollTop, tableScrollBottom);
        });
        tableScrollBottom.addEventListener('scroll', () => {
            synchronizeScroll(tableScrollBottom, tableScrollTop);
        });

        updateScrollWidth();
        window.addEventListener('resize', updateScrollWidth);
        if (typeof ResizeObserver !== 'undefined') {
            const tableResizeObserver = new ResizeObserver(updateScrollWidth);
            tableResizeObserver.observe(table);
            tableResizeObserver.observe(tableScrollBottom);
        }
    }

    // 4. グラフ描画機能 (Chart.js)
    if (typeof chartData !== 'undefined' && document.getElementById('sensorChart')) {
        const ctx = document.getElementById('sensorChart').getContext('2d');

        const labels = chartData['timestamp'];
        const datasets = [];

        // 複数グラフ表示用のカラーパレット
        const colors = [
            'rgb(255, 99, 132)', 'rgb(54, 162, 235)', 'rgb(255, 206, 86)',
            'rgb(75, 192, 192)', 'rgb(153, 102, 255)', 'rgb(255, 159, 64)',
            'rgb(201, 203, 207)', 'rgb(100, 255, 100)'
        ];
        let colorIndex = 0;

        // JSONデータからデータセットを構築
        for (const key in chartData) {
            if (key !== 'timestamp' && key !== 'trigger') {
                datasets.push({
                    label: key,
                    data: chartData[key],
                    borderColor: colors[colorIndex % colors.length],
                    backgroundColor: colors[colorIndex % colors.length],
                    tension: 0.2, // 少し線をなめらかにする
                    fill: false
                });
                colorIndex++;
            }
        }

        // グラフの描画
        new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: datasets
            },
            options: {
                responsive: true,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                scales: {
                    x: {
                        display: true,
                        title: {
                            display: true,
                            text: 'Timestamp'
                        }
                    },
                    y: {
                        display: true,
                        title: {
                            display: true,
                            text: 'Value'
                        }
                    }
                }
            }
        });
    }
});
