/* jshint esversion: 6 */
/* globals LineGraph, SubGraph, Series */
(function () {
"use strict";

const COLORS = [
	["#CC0000", "#0100CC"],
	["#CC6700", "#6600CC"],
	["#CC0067", "#0067CC"],
	["#CC3200", "#3300CC"],
	["#CC0033", "#0010CC"],
];

class HistoryManager {
	constructor() {
		this._graph = null;
		this._mode = null;

		this._tempGraph = new SubGraph("Temperature", "°C", -10, 40);
		this._humGraph = new SubGraph("Humidity", "%", 0, 100);

		this._xhr = new XMLHttpRequest();
		this._xhr.onreadystatechange = () => {
			this._xhrStateChange(this._xhr);
		};
		this._xhr.responseType = 'json';

		['temp', 'hum', 'both'].forEach((name) => {
			const item = document.getElementById('mode-' + name);
			item.addEventListener('change', (evt) => {
				this._setMode(name);
			});
		});
	}

	setup(graph) {
		this._graph = graph;
		this._setMode('temp');
		graph.setDelegate(this);
	}

	rangeUpdated(startTimeMs, endTimeMs) {
		const startTime = Math.round(startTimeMs / 1000);
		const endTime = Math.round(endTimeMs / 1000);
		const xhr = this._xhr;

		this._graph.setBusyWithMessage("Loading, please wait…");

		xhr.open('GET', 'data?start=' + startTime + '&end=' + endTime, true);
		xhr.send(null);
	}

	_xhrStateChange(xhr) {
		if (xhr.readyState != 4) {
			return;
		}

		if (xhr.status != 200) {
			this._error("Load error: bad status " + xhr.status + " " +
				xhr.statusText);
			return;
		}

		const contentType = xhr.getResponseHeader('Content-Type');

		if (contentType != 'application/json') {
			this._error("Load error: bad content type " + contentType);
			return;
		}

		const data = xhr.response;

		if (data === null) {
			this._error("Load error: invalid JSON received");
			return;
		}

		const tempSeries = this._tempGraph.series;
		const humSeries = this._humGraph.series;

		tempSeries.length = 0;
		humSeries.length = 0;

		let idx = 0;
		let maxTemp = null;
		let minTemp = null;
		data.forEach((tempHumData) => {
			const name = tempHumData.name;
			const [tempColor, humColor] = COLORS[idx];
			const tempValues = [];
			const humValues = [];

			tempHumData.tstamps.map((tstamp, i) => {
				tstamp *= 1000;

				const curTemp = tempHumData.temps[i];
				const curHum = tempHumData.hums[i];

				tempValues.push({
					timeStampMs: tstamp,
					value: curTemp,
				});

				humValues.push({
					timeStampMs: tstamp,
					value: curHum,
				});

				if (maxTemp === null || maxTemp < curTemp) {
					maxTemp = curTemp;
				}

				if (minTemp === null || minTemp > curTemp) {
					minTemp = curTemp;
				}

			});

			tempSeries.push(new Series(name, tempColor, tempValues));
			humSeries.push(new Series(name, humColor, humValues));

			idx += 1;
			if (idx >= COLORS.length) {
				idx = 0;
			}
		});

		maxTemp = Math.ceil(maxTemp);
		minTemp = Math.floor(minTemp);

		this._tempGraph.min = minTemp;
		this._tempGraph.max = maxTemp;

		this._graph.refresh();
	}

	_setMode(newMode) {
		const graph = this._graph;
		const subGraphs = graph.subGraphs;

		this._mode = newMode;

		subGraphs.length = 0;

		if (newMode === 'temp' || newMode === 'both') {
			subGraphs.push(this._tempGraph);
		}

		if (newMode === 'hum' || newMode === 'both') {
			subGraphs.push(this._humGraph);
		}

		if (!graph.busy) {
			graph.refresh();
		}
	}

	_error(message) {
		this._graph.setBusyWithMessage(message);
	}
}



document.addEventListener('DOMContentLoaded', (event) => {
	const graph = new LineGraph('history');
	const manager = new HistoryManager();
	manager.setup(graph);
});
}());
