/* jshint esversion: 6 */
/* globals LineGraph, Axis, Series */
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
	constructor(graph) {
		this._graph = graph;
		this._mode = null;

		this._tempAxis = new Axis("Temperature (°C)", "#FF0000", -10, 40);
		this._humAxis = new Axis("Humidity (%)", "#0000FF", 0, 100);
		this._tempSeries = [];
		this._humSeries = [];

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

		this._setMode('temp');
	}

	initialLoad() {
		this._graph.initLoad();
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

		const tempSeries = this._tempSeries;
		const humSeries = this._humSeries;

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

			tempSeries.push(new Series(name, tempColor, this._tempAxis,
				tempValues));
			humSeries.push(new Series(name, humColor, this._humAxis,
				humValues));

			idx += 1;
			if (idx >= COLORS.length) {
				idx = 0;
			}
		});

		maxTemp = Math.ceil(maxTemp / 5) * 5;
		minTemp = Math.floor(minTemp / 5) * 5;
		maxTemp = minTemp + Math.ceil((maxTemp - minTemp) / 10) * 10;

		this._tempAxis.min = minTemp;
		this._tempAxis.max = maxTemp;

		this._updateGraph();
	}

	_setMode(newMode) {
		const graph = this._graph;

		this._mode = newMode;

		if (newMode === 'temp') {
			graph.leftAxis = null;
			graph.rightAxis = this._tempAxis;
		} else if (newMode === 'hum') {
			graph.leftAxis = this._humAxis;
			graph.rightAxis = null;
		} else {
			graph.leftAxis = this._humAxis;
			graph.rightAxis = this._tempAxis;
		}

		if (!graph.busy) {
			this._updateGraph();
		}
	}

	_updateGraph() {
		const graph = this._graph;
		const graphSeries = graph.series;
		const mode = this._mode;

		graphSeries.length = 0;

		if (mode === 'hum' || mode === 'both') {
			this._humSeries.forEach((item) => { graphSeries.push(item); });
		}

		if (mode === 'temp' || mode === 'both') {
			this._tempSeries.forEach((item) => { graphSeries.push(item); });
		}

		graph.refresh();
	}

	_error(message) {
		this._graph.setBusyWithMessage(message);
	}
}



document.addEventListener('DOMContentLoaded', (event) => {
	const graph = new LineGraph('history');
	const manager = new HistoryManager(graph);
	graph.delegate = manager;
	manager.initialLoad();

});
}());
