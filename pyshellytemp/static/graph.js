/* Graph canvas implementation */
/* jshint esversion: 6 */
/* exported LineGraph, SubGraph, Series */

const DEFAULT_DISP_INTERVAL = 3 * 86400 * 1000;

/**
 * Main class of the line graph. Defines the X axis of the graph (time) and
 * handles the buttons. May contain an arbitrary numbers of sub-graphs in the
 * subGraphs attribute.
 * Modifications to the time interval, the sub-graphs, or their series requires
 * calling refresh on the main graph.
 * The graph has a delegate that is called when the time interval is changed;
 * the main code should update the series with new data and call refresh.
 */
class LineGraph {
	constructor(canvasID) {
		const canvas = document.getElementById(canvasID);

		const ctx = canvas.getContext('2d', {
			alpha: false,
		});

		// User-configurable
		this.axisLabelFont = '10pt Verdana, Arial, sans-serif';
		this.axisValueFont = '8pt Verdana, Arial, sans-serif';
		this.legendFont = this.axisValueFont;
		this.legendFont = this.axisValueFont;
		this.gridColor = '#999999';
		this.subGridColor = '#CCCCCC';
		this.textColor = '#000000';
		this.marginPx = 5;
		this.legendOffset = 15;

		// Time range
		this.endTimeMs = Date.now();
		this.startTimeMs = this.endTimeMs - DEFAULT_DISP_INTERVAL;

		// Sub-graphs
		this.subGraphs = [];

		// Private attributes
		this._context = ctx;
		this._delegate = null;
		this._width = canvas.width;
		this._height = canvas.height;
		this._dispHeight = null;
		this._dispWidth = null;
		this._totalIntervalMs = null;
		this._timeValY = null;
		this._timeValWidth = null;
		this._timeValIntervalMs = null;
		this._markerHighLight = null;
		this._firstMarkerTime = null;
		this._firstMarkerX = null;
		this._markerSpacing = null;
		this._canvasScrollInfo = null;
		this._legendInfo = null;
		this._legendState = {
			posX: null,
			posY: null,
			tstamp: null,
			dispDate: null,
			dispValues: [],
		};


		// Handle legend
		canvas.addEventListener('mousemove', (evt) => {
			this._handleMouseMoveLegend(evt);
		});

		canvas.addEventListener('mouseout', (evt) => {
			this._handleMouseOutLegend(evt);
		});

		// Handle canvas drag-scroll
		canvas.addEventListener('mousedown', (evt) => {
			this._handleMouseDown(evt);
		});

		// Handle canvas buttons
		HANDLERS.forEach((item) => {
			const [idSuffix, funcSuffix] = item;
			const button = document.getElementById(canvasID + '-' + idSuffix);

			if (button === null) {
				return;
			}

			const targetFunc = this['_handle' + funcSuffix];

			button.addEventListener('click', (evt) => {
				if (!this._busy) {
					targetFunc.apply(this);
				}
			});
		});

		// Set-up HiDPI canvas
		const ratio = window.devicePixelRatio;
		canvas.style.width = canvas.width + 'px';
		canvas.style.height = canvas.height + 'px';

		canvas.width *= ratio;
		canvas.height *= ratio;

		ctx.scale(ratio, ratio);
	}

	/**
	 * Sets the graph delegate. The delegate’s rangeUpdated method will be
	 * immediately called with the graph’s current range.
	 */
	setDelegate(delegate) {
		this._delegate = delegate;
		this._notifyRangeCallback();
	}

	/**
	 * Refreshes the graph display.
	 */
	refresh() {
		const ctx = this._context;
		ctx.textBaseline = 'alphabetic';

		this._busy = false;

		// Determine the required height for the time values
		ctx.font = this.axisValueFont;
		// Bottom labels can display either a year, MM/DD, or HH:MM;
		// XX/XX is slightly larger
		const timeMetrics = ctx.measureText('XX/XX');
		const timeTextHeight = Math.ceil(timeMetrics.actualBoundingBoxAscent +
			timeMetrics.actualBoundingBoxDescent);
		const timeTextWidth = Math.ceil(timeMetrics.width);

		// Determine Y values for the bottom of the graph; store value width
		this._dispHeight = this._height - timeTextHeight - this.marginPx;
		this._timeValY = this._height - timeTextHeight +
			Math.floor(timeMetrics.actualBoundingBoxAscent);
		this._timeValWidth = timeTextWidth;

		// Determine the available space for each sub-graph; leave sufficient
		// margin between them so values cannot clash
		const subGraphCount = this.subGraphs.length;
		const subGraphMargin = timeTextHeight + this.marginPx;
		const subGraphHeight = Math.floor((this._dispHeight - subGraphMargin *
			(subGraphCount - 1)) / subGraphCount);
		const subGraphStride = subGraphHeight + subGraphMargin;

		// Layout the sub-graphs by giving each of them its Y position and
		// height. The call returns layout information for the sub-graph, which
		// is combined between all sub-graphs.
		let maxValWidth = 0;
		let maxTitleWidth = 0;
		let curY = 0;
		this.subGraphs.forEach((subGraph) => {
			const layoutInfo = subGraph.layout(ctx, curY, subGraphHeight,
				this.axisLabelFont, this.axisValueFont);

			maxValWidth = Math.max(maxValWidth, layoutInfo.maxValWidth);
			maxTitleWidth = Math.max(maxTitleWidth, layoutInfo.maxTitleWidth);

			curY += subGraphStride;
		});

		this._subTitleX = this._width - maxTitleWidth;
		this._subValX = this._subTitleX - this.marginPx - maxValWidth;
		this._dispWidth = this._subValX - this.marginPx;

		// Layout the legend
		this._layoutLegend();

		// Layout the time markers and redraw
		this._recalcTimeMarkersAndRedraw();
	}

	/**
	 * Display the specified message on the graph and stop updates to it until
	 * refresh is called.
	 */
	setBusyWithMessage(message) {
		const centerX = Math.round(this._width / 2);
		const centerY = Math.round(this._height / 2);
		const ctx = this._context;

		ctx.textAlign = 'center';
		ctx.textBaseline = 'middle';
		ctx.font = this.axisLabelFont;

		const metrics = ctx.measureText(message);
		const textWidth = Math.ceil(metrics.width);
		const textHeight = Math.ceil(metrics.actualBoundingBoxAscent +
			metrics.actualBoundingBoxDescent);

		ctx.fillStyle = 'white';
		ctx.fillRect(Math.floor(centerX - textWidth / 2), Math.floor(centerY -
			textHeight / 2), textWidth, textHeight);

		ctx.fillStyle = this.textColor;
		ctx.fillText(message, centerX, centerY);

		this._busy = true;
	}

	// Called when the time interval changes (because of drag scroll, zoom/move
	// button click, or initial delegate set)
	_notifyRangeCallback() {
		// Load extra data from the margins so the the graph line start at least
		// at the edge of the graph; include at least 12 hours of margin
		const extraTime = Math.max(12 * 3600000,
			Math.floor((this.endTimeMs - this.startTimeMs) * 0.05));

		this._delegate.rangeUpdated(this.startTimeMs - extraTime,
			this.endTimeMs + extraTime);
	}

	// Recalculates the position of the time markers (if needed) and redraw the
	// graphs.
	// This assumes that other items are already laid out.
	_recalcTimeMarkersAndRedraw() {
		// Set the context to consistent values
		const ctx = this._context;
		ctx.lineWidth = 1.0;
		ctx.textAlign = 'start';
		ctx.textBaseline = 'alphabetic';

		// Time markers layout
		const totalIntervalMs = this.endTimeMs - this.startTimeMs;
		let intervalMs;
		let markerHighLight;

		if (totalIntervalMs !== this._totalIntervalMs) {
			// Recalculate the interval between vertical time markers
			const minMarkerSpacing = Math.ceil(this._timeValWidth +
				this.marginPx);
			const maxTimeMarkers = Math.floor(this._width / minMarkerSpacing);
			const secsBetweenMarkers = Math.floor(totalIntervalMs / 1000 /
				maxTimeMarkers);
			const minsBetweenMarkers = Math.floor(secsBetweenMarkers / 60);
			const foundInterval = INTERVALS.find((val) =>
				val >= minsBetweenMarkers);

			if (foundInterval === undefined) {
				// Round to N days and use that
				intervalMs = Math.ceil(secsBetweenMarkers / 86400) * 86400 *
					1000;
			} else {
				// Use the determined “nice” interval
				intervalMs = foundInterval * 60 * 1000;
			}

			if (intervalMs >= 365 * 86400 * 1000) {
				// More than one year between markers, do not highlight any of
				// them
				markerHighLight = {
					day: false,
					month: false,
					year: false,
				};
			} else if (intervalMs >= 30 * 86400 * 1000) {
				// More than one month between markers, only highlight years
				markerHighLight = {
					day: false,
					month: false,
					year: true,
				};
			} else if (intervalMs >= 86400 * 1000) {
				// More than one day between markers, highlight months and years
				markerHighLight = {
					day: false,
					month: true,
					year: true,
				};
			} else {
				// Highlight days, month and years
				markerHighLight = {
					day: true,
					month: true,
					year: true,
				};
			}

			this._timeValIntervalMs = intervalMs;
			this._totalIntervalMs = totalIntervalMs;
			this._markerHighLight = markerHighLight;
		} else {
			intervalMs = this._timeValIntervalMs;
		}

		// Reposition the markers
		const graphWidth = this._dispWidth;
		const firstMarkerTime = Math.ceil(this.startTimeMs / intervalMs) *
			intervalMs;
		const firstMarkerX = (firstMarkerTime - this.startTimeMs) *
			graphWidth / totalIntervalMs;
		const markerSpacing = intervalMs * graphWidth / totalIntervalMs;

		this._firstMarkerTime = firstMarkerTime;
		this._firstMarkerX = firstMarkerX;
		this._markerSpacing = markerSpacing;

		this._redraw();
	}

	_redraw() {
		const ctx = this._context;
		const firstMarkerTime = this._firstMarkerTime;
		const firstMarkerX = this._firstMarkerX;
		const markerSpacing = this._markerSpacing;
		const intervalMs = this._timeValIntervalMs;
		const markerHighLight = this._markerHighLight;

		// Clear all
		ctx.lineWidth = 1.0;
		ctx.textAlign = 'start';
		ctx.textBaseline = 'alphabetic';
		ctx.fillStyle = 'white';
		ctx.fillRect(0, 0, this._width, this._height);

		// Time markers and vertical lines
		const startTime = new Date(this.startTimeMs);
		let prevYear = startTime.getFullYear();
		let prevMonth = startTime.getMonth() + 1;
		let prevDay = startTime.getDate();
		let curTime = new Date(firstMarkerTime);
		let curX = firstMarkerX;

		ctx.textAlign = 'center';
		ctx.font = this.axisValueFont;
		ctx.strokeStyle = this.gridColor;
		ctx.fillStyle = this.textColor;

		ctx.beginPath();
		while (curX <= this._dispWidth) {
			const curYear = curTime.getFullYear();
			const curMonth = curTime.getMonth() + 1;
			const curDay = curTime.getDate();
			let label;
			let highlight;

			if (curYear != prevYear) {
				label = '' + curYear;
				highlight = markerHighLight.year;
			} else if (curMonth != prevMonth) {
				label = LineGraph._ZeroPad(curDay) + '/' +
					LineGraph._ZeroPad(curMonth);
				highlight = markerHighLight.month;
			} else if (curDay != prevDay) {
				label = LineGraph._ZeroPad(curDay) + '/' +
					LineGraph._ZeroPad(curMonth);
				highlight = markerHighLight.day;
			} else {
				label = LineGraph._ZeroPad(curTime.getHours()) + ':' +
					LineGraph._ZeroPad(curTime.getMinutes());
				highlight = false;
			}

			const drawX = Math.round(curX) + 0.5;
			ctx.moveTo(drawX, this._dispHeight);
			ctx.lineTo(drawX, 0);

			if (highlight) {
				ctx.font = 'bold ' + this.axisValueFont;
			} else {
				ctx.font = this.axisValueFont;
			}

			ctx.fillText(label, drawX, this._timeValY);

			curTime = new Date(curTime.getTime() + intervalMs);
			curX += markerSpacing;
			prevYear = curYear;
			prevMonth = curMonth;
			prevDay = curDay;
		}

		ctx.stroke();

		ctx.textAlign = 'start';
		ctx.textBaseline = 'middle';
		ctx.font = this.axisValueFont;

		// Subgraphs (including their titles, legends, and horizontal lines)
		this.subGraphs.forEach((subGraph) => {
			subGraph.draw(ctx, this._dispWidth, this._subValX, this._subTitleX,
				this.startTimeMs, this.endTimeMs, this.gridColor,
				this.subGridColor);
		});

		// Legend
		this._drawLegend();
	}

	_layoutLegend() {
		const ctx = this._context;

		let seriesInfo = [];
		this.subGraphs.forEach((subGraph) => {
			subGraph.collectSeriesInfo(seriesInfo);
		});

		let dateTextMetric = ctx.measureText("XX/XX/XXXX XX:XX");
		let dateTextWidth = Math.ceil(dateTextMetric.width);

		let maxTextWidth = 0;
		let maxTextHeight = Math.ceil(dateTextMetric.actualBoundingBoxAscent +
			dateTextMetric.actualBoundingBoxDescent);
		let legendValues = [];

		seriesInfo.forEach((info) => {
			const textMetric = ctx.measureText(info.name);
			const textWidth = Math.ceil(textMetric.width);
			const textHeight = Math.ceil(textMetric.actualBoundingBoxAscent +
				textMetric.actualBoundingBoxDescent);

			if (maxTextWidth < textWidth) {
				maxTextWidth = textWidth;
			}

			if (maxTextHeight < textHeight) {
				maxTextHeight = textHeight;
			}

			legendValues.push("—");
		});

		const colorCellX = 1 + this.marginPx;
		const textX = colorCellX + maxTextHeight + this.marginPx;
		const valueX = textX + maxTextWidth + this.marginPx;
		const baseWidth = valueX + this.marginPx + 1;
		const lineSpacing  = maxTextHeight + this.marginPx;
		const height = 2 + this.marginPx + lineSpacing *
			(seriesInfo.length + 1);

		// The actual width of the legend is its base width + the width of the
		// widest value; however, if the legend text and values are very short,
		// the date text can be the longest line. Calculate the minimal value
		// width so that the date text fits.
		const baseValueWidth = dateTextWidth - (maxTextHeight + this.marginPx +
			maxTextWidth + this.marginPx);

		this._legendInfo = {
			seriesInfo: seriesInfo,
			colorCellX: colorCellX,
			colorCellSize: maxTextHeight,
			textX: textX,
			valueX: valueX,
			baseWidth: baseWidth,
			baseValueWidth: baseValueWidth,
			height: height,
			lineSpacing: lineSpacing,
		}
	}

	_drawLegend() {
		const ctx = this._context;
		const legendInfo = this._legendInfo;
		const legendState = this._legendState;
		const legendOffset = this.legendOffset;

		let x = legendState.posX;
		let y = legendState.posY;

		if (this._canvasScrollInfo !== null || this._busy || x === null) {
			return;
		}

		let maxValueWidth = legendInfo.baseValueWidth;
		legendState.dispValues.forEach((value) => {
			const valueMetric = ctx.measureText('' + value);
			const valueWidth = Math.ceil(valueMetric.width);
			if (valueWidth > maxValueWidth) {
				maxValueWidth = valueWidth;
			}
		});

		const legendWidth = legendInfo.baseWidth + maxValueWidth;
		ctx.globalAlpha = 0.8;

		// Draw a vertical line at the mouse position
		ctx.lineWidth = 1;
		ctx.strokeStyle = this.gridColor;
		ctx.beginPath();
		ctx.moveTo(x + 0.5, 0.5);
		ctx.lineTo(x + 0.5, this._dispHeight + 0.5);
		ctx.stroke();

		// Adjust the legend position so it stays visible
		if ((x + legendOffset + legendWidth) > this._width) {
			x -= legendOffset + legendWidth;
		} else {
			x += legendOffset;
		}

		if ((y + legendOffset + legendInfo.height) > this._height) {
			y -= legendOffset + legendInfo.height;
		} else {
			y += legendOffset;
		}

		ctx.textBaseline = 'top';
		ctx.font = this.legendFont;
		ctx.strokeStyle = this.textColor;
		ctx.fillStyle = '#FFFFFF';
		ctx.fillRect(x, y, legendWidth, legendInfo.height);
		ctx.strokeRect(x + 0.5, y + 0.5, legendWidth, legendInfo.height);

		const colorCellSize = legendInfo.colorCellSize;

		let posY = y + 1 + this.marginPx;
		ctx.fillStyle = this.textColor;
		ctx.fillText(legendState.dispDate, x + legendInfo.colorCellX, posY);

		legendInfo.seriesInfo.forEach((entry, idx) => {
			let posX = x + legendInfo.colorCellX;
			let value = legendState.dispValues[idx];
			posY += legendInfo.lineSpacing;

			ctx.fillStyle = entry.color;

			ctx.fillRect(posX, posY, colorCellSize, colorCellSize);
			ctx.strokeRect(posX + 0.5, posY + 0.5, colorCellSize,
					colorCellSize);

			ctx.fillStyle = this.textColor;

			posX = x + legendInfo.textX;
			ctx.fillText(entry.name, posX, posY);

			posX = x + legendInfo.valueX;
			ctx.fillText("" + value, posX, posY);
		});

		ctx.globalAlpha = 1;
	}

	_handleMouseDown(evt) {
		if (evt.offsetX > this._dispWidth || evt.offsetY > this._dispHeight ||
			this._busy) {
			return;
		}

		const scrollInfo = {
			origX: evt.clientX,
			origEnd: this.endTimeMs,
			limitEnd: Date.now(),
			timeInterval: this._totalIntervalMs,
			curDelta: 0,
			moveHandler: (evt) => {
				this._handleMouseMoveDrag(evt);
			},
			upHandler: (evt) =>  {
				this._handleMouseUp(evt);
			},
		};

		window.addEventListener('mousemove', scrollInfo.moveHandler);
		window.addEventListener('mouseup', scrollInfo.upHandler);

		this._canvasScrollInfo = scrollInfo;
	}

	_handleMouseMoveDrag(evt) {
		const scrollInfo = this._canvasScrollInfo;
		const delta = evt.clientX - scrollInfo.origX;

		if (delta === scrollInfo.curDelta) {
			return;
		}

		scrollInfo.curDelta = delta;

		const graphWidth = this._dispWidth;
		const timeDelta = scrollInfo.timeInterval * delta / graphWidth;

		let newEndTime = scrollInfo.origEnd - timeDelta;
		if (newEndTime > scrollInfo.limitEnd) {
			newEndTime = scrollInfo.limitEnd;
		}

		if (this.endTimeMs == newEndTime) {
			return;
		}

		this.endTimeMs = newEndTime;
		this.startTimeMs = newEndTime - scrollInfo.timeInterval;

		this._recalcTimeMarkersAndRedraw();
	}

	_handleMouseUp(evt) {
		const scrollInfo = this._canvasScrollInfo;
		this._canvasScrollInfo = null;

		window.removeEventListener('mousemove', scrollInfo.moveHandler);
		window.removeEventListener('mouseup', scrollInfo.upHandler);

		if (scrollInfo.curDelta !== 0) {
			this._updateLegendPos(evt);
			this._notifyRangeCallback();
		}
	}

	_handleMouseMoveLegend(evt) {
		const needsRedraw = this._updateLegendPos(evt);

		if (needsRedraw) {
			this._redraw();
		}
	}

	_handleMouseOutLegend(evt) {
		if (this._legendState.posX !== null) {
			this._legendState.posX = null;
			this._redraw();
		}
	}

	_updateLegendPos(evt) {
		const posX = evt.offsetX;
		const posY = evt.offsetY;
		const legendState = this._legendState;

		if (this._canvasScrollInfo || this._busy) {
			return;
		}

		if (posX > this._dispWidth || posY > this._dispHeight) {
			if (legendState.posX !== null) {
				legendState.posX = null;
				this.refresh();
			}

			return;
		}

		let needsRedraw = false;

		if (legendState.posX !== posX) {
			legendState.posX = posX;

			const tOffset = posX * this._totalIntervalMs / this._dispWidth;
			const tstamp = Math.round((this.startTimeMs + tOffset) / 60000) *
				60000;

			if (tstamp !== legendState.tstamp) {
				const dt = new Date(tstamp);
				legendState.tstamp = tstamp;
				legendState.dispDate = LineGraph._ZeroPad(dt.getDate()) + '/' +
					LineGraph._ZeroPad(dt.getMonth() + 1) + '/' +
					dt.getFullYear() + ' ' + LineGraph._ZeroPad(dt.getHours()) +
					':' + LineGraph._ZeroPad(dt.getMinutes());

				legendState.dispValues.length = 0;
				this.subGraphs.forEach((subGraph) => {
					subGraph.collectValues(tstamp, legendState.dispValues);
				});

				needsRedraw = true;
			}
		}

		if (legendState.posY !== posY) {
			legendState.posY = posY;
			needsRedraw = true;
		}

		return needsRedraw;
	}

	_handleZoomPlus() {
		const curIntervalMs = this._totalIntervalMs;
		const newIntervalMs = Math.round(curIntervalMs / 2);
		if (newIntervalMs <= 60 * 60 * 1000) {
			return;
		}

		const newEndTimeMs = this.endTimeMs - Math.round(newIntervalMs / 2);
		this.endTimeMs = newEndTimeMs;
		this.startTimeMs = newEndTimeMs - newIntervalMs;

		this._recalcTimeMarkersAndRedraw();
		this._notifyRangeCallback();
	}

	_handleZoomMinus() {
		const curIntervalMs = this._totalIntervalMs;
		const newIntervalMs = curIntervalMs * 2;
		const curDateTimeMs = Date.now();

		let newEndTimeMs = this.endTimeMs + Math.round(curIntervalMs / 2);
		if (newEndTimeMs > curDateTimeMs) {
			newEndTimeMs = curDateTimeMs;
		}
		this.endTimeMs = newEndTimeMs;
		this.startTimeMs = newEndTimeMs - newIntervalMs;

		this._recalcTimeMarkersAndRedraw();
		this._notifyRangeCallback();
	}

	_handleMoveLeft() {
		const curIntervalMs = this._totalIntervalMs;
		const delta = Math.round(curIntervalMs * 3 / 4);

		this.endTimeMs -= delta;
		this.startTimeMs -= delta;

		this._recalcTimeMarkersAndRedraw();
		this._notifyRangeCallback();
	}

	_handleMoveRight() {
		const curIntervalMs = this._totalIntervalMs;
		const curDateTimeMs = Date.now();

		let newEndTimeMs = this.endTimeMs + Math.round(curIntervalMs * 3 / 4);
		if (newEndTimeMs > curDateTimeMs) {
			newEndTimeMs = curDateTimeMs;
		}
		this.endTimeMs = newEndTimeMs;
		this.startTimeMs = newEndTimeMs - curIntervalMs;

		this._recalcTimeMarkersAndRedraw();
		this._notifyRangeCallback();
	}

	_handleDispDay() {
		const curDateTimeMs = Date.now();

		this.endTimeMs = curDateTimeMs;
		this.startTimeMs = curDateTimeMs - 24 * 3600 * 1000;

		this._recalcTimeMarkersAndRedraw();
		this._notifyRangeCallback();
	}

	_handleDispDefault() {
		const curDateTimeMs = Date.now();

		this.endTimeMs = curDateTimeMs;
		this.startTimeMs = curDateTimeMs - DEFAULT_DISP_INTERVAL;

		this._recalcTimeMarkersAndRedraw();
		this._notifyRangeCallback();
	}

	_handleDispMonth() {
		const curDateTimeMs = Date.now();

		this.endTimeMs = curDateTimeMs;
		this.startTimeMs = curDateTimeMs - 31 * 24 * 3600 * 1000;

		this._recalcTimeMarkersAndRedraw();
		this._notifyRangeCallback();
	}

	_handleDispYear() {
		const curDateTimeMs = Date.now();

		this.endTimeMs = curDateTimeMs;
		this.startTimeMs = curDateTimeMs - 365 * 24 * 3600 * 1000;

		this._recalcTimeMarkersAndRedraw();
		this._notifyRangeCallback();
	}

	_handleExport() {
		let link = document.createElement('a');
		let dateText = new Date(this.startTimeMs).toISOString();
		dateText = dateText.replace(/:/g, '-').replace('T', '_');
		dateText = dateText.replace(/\..+$/, '');

		link.download = 'history_' + dateText + '.png';
		link.href = this._context.canvas.toDataURL();
		link.click();
	}
}

// Zero-padding function, used internally
LineGraph._ZeroPad = function (value) {
	if (value < 10) {
		return '0' + value;
	}

	return '' + value;
};

/**
 * A sub-graph of the main line graph. Sub-graphs share their X axis with the
 * main graph but have their own Y axis, horizontal lines, and legend.
 * They need to be in the main graph’s subgraphs attribute in order to be
 * displayed.
 * The series that need to be displayed by the sub-graph are specified in the
 * series attribute (list). When they are modified, the refresh method on the
 * main graph needs to be called.
 */
class SubGraph {
	constructor(title, unit, min, max) {
		if (unit) {
			title += " (" + unit + ")";
		}

		this.title = title;
		this.unit = unit;
		this.min = min;
		this.max = max;
		this.series = [];

		this._posY = null;
		this._height = null;
		this._axisLabelFont = null;
		this._axisValueFont = null;
		this._titleX = null;
		this._titleY = null;
		this._actualMin = null;
		this._actualRange = null;
		this._lineIntervalPx = null;
		this._lineIntervalUnit = null;
		this._subLineCount = null;
	}

	/**
	 * Called by the main graph to lay the sub-graph out. Takes the Y position
	 * of the sub-graph and its height; returns the layout info for the
	 * sub-graph.
	 */
	layout(ctx, posY, height, axisLabelFont, axisValueFont) {
		// Measure texts to be displayed later
		ctx.font = axisLabelFont;
		const lMetrics = ctx.measureText(this.title);

		const labelOffset = Math.floor((height - lMetrics.width) / 2);
		const labelTextHeight = Math.ceil(lMetrics.actualBoundingBoxAscent +
			lMetrics.actualBoundingBoxDescent);

		ctx.font = axisValueFont;
		const minValueWidth = ctx.measureText('' + this.min).width;
		const maxValueWidth = ctx.measureText('' + this.max).width;
		const valueMaxWidth = Math.ceil(Math.max(minValueWidth, maxValueWidth));

		// Layout general position, title, and save fonts
		this._posY = posY;
		this._height = height;
		this._axisLabelFont = axisLabelFont;
		this._axisValueFont = axisValueFont;
		this._titleX = -height -posY + labelOffset;
		this._titleY = lMetrics.actualBoundingBoxAscent;

		// Determine the position of the horizontal lines
		const maxLines = Math.floor(height / (labelTextHeight * 1.5));
		const range = this.max > this.min ? (this.max - this.min) : maxLines;
		const rawInterval = Math.max(range / maxLines, 1);
		let actInterval = Math.pow(10, Math.ceil(Math.log10(rawInterval)));
		let halfInterval = actInterval / 2;
		if (rawInterval <= halfInterval && Number.isInteger(halfInterval)) {
			actInterval = halfInterval;
		}

		const adjMin = Math.floor(this.min / actInterval) * actInterval;
		const adjRange = this.max - adjMin;
		const lineIntervalPx = height * actInterval / adjRange;

		this._actualMin = adjMin;
		this._actualRange = adjRange;
		this._lineIntervalPx = lineIntervalPx;
		this._lineIntervalUnit = actInterval;

		// Add sub-lines if the interval between sub-lines is at least 10 px
		// With the normal interval (power of 10), try to use 10 lines
		// If that does not fit or the half-interval is used, try to use 5 lines
		// If that does not fit, try to add use 2 lines
		if ((actInterval != halfInterval) && lineIntervalPx >= 100) {
			this._subLineCount = 9;
			this._lineIntervalPx = this._lineIntervalPx / 10;
		} else if (lineIntervalPx >= 50) {
			this._subLineCount = 4;
			this._lineIntervalPx = this._lineIntervalPx / 5;
		} else if (lineIntervalPx >= 20) {
			this._subLineCount = 1;
			this._lineIntervalPx = this._lineIntervalPx / 2;
		} else {
			this._subLineCount = 0;
		}

		// Return layout info to caller
		return {
			maxValWidth: valueMaxWidth,
			maxTitleWidth: labelTextHeight,
		}
	}

	/**
	 * Draws the sub-graph.
	 * Assumes that the context fillStyle is set to the text color and
	 * that textBaseline is 'middle'.
	 */
	draw(ctx, dispWidth, valueX, titleXOffset, startTimeMs, endTimeMs,
		gridColor, subGridColor) {
		ctx.lineWidth = 1.0;

		// Draw the sub-graph title
		ctx.save();
		ctx.textBaseline = 'alphabetic';
		ctx.font = this._axisLabelFont;
		ctx.rotate(-Math.PI / 2);
		ctx.fillText(this.title, this._titleX, this._titleY + titleXOffset);
		ctx.restore();

		// Draw the horizontal lines and markers
		let curPos = this._posY + this._height;
		let curVal = this._actualMin;
		let curSubLine = 0;

		while (curPos >= this._posY) {
			ctx.beginPath();

			let lineY = Math.round(curPos) + 0.5;
			ctx.moveTo(0, lineY);
			ctx.lineTo(dispWidth, lineY);

			if (curSubLine == 0) {
				// Main line
				ctx.strokeStyle = gridColor;

				const strVal = '' + curVal;
				let textY = lineY;
				if (curVal === this.max) {
					// This is the top value, try to not draw it outside of the
					// borders of the screen
					const metrics = ctx.measureText(strVal)
					const ascent = metrics.actualBoundingBoxAscent;
					const topY = textY - ascent;
					if (topY < 0) {
						textY -= topY;
					}
				}

				ctx.fillText(strVal, valueX, textY);
				curVal += this._lineIntervalUnit;
				curSubLine = this._subLineCount;
			} else {
				// Sub-line
				ctx.strokeStyle = subGridColor;

				curSubLine -= 1;
			}

			ctx.stroke();

			curPos -= this._lineIntervalPx;
		}

		// Draw the series
		const timeFactor = dispWidth / (endTimeMs - startTimeMs);
		const heightFactor = this._height / this._actualRange;
		const baseY = this._posY + this._height;

		ctx.save();
		ctx.lineWidth = 2.0;
		ctx.rect(0, this._posY, dispWidth, this._height);
		ctx.clip();

		this.series.forEach((series) => {
			series.draw(ctx, startTimeMs, timeFactor, baseY, this._actualMin,
				heightFactor);
		});

		ctx.restore();
	}

	/**
	 * Collects series information for the legend.
	 */
	collectSeriesInfo(seriesInfo) {
		this.series.forEach((series) => {
			series.collectSeriesInfo(seriesInfo);
		});
	}

	/**
	 * Collect values for the specified time. The values are added in the same
	 * order as collectSeriesInfo.
	 */
	collectValues(tstamp, values) {
		this.series.forEach((series) => {
			series.collectValues(tstamp, this.unit, values);
		});
	}
}


/**
 * Value series.
 * Constructed from a name, a color, and an array of {timeStampMs: …, value: …}
 * objects. The series will be displayed on the sub-graph it is part of.
 */
class Series {
	constructor(name, color, values) {
		this.name = name;
		this.color = color;
		this.values = values;
	}

	/**
	 * Draw the series in the provided context
	 */
	draw(ctx, startTimeMs, timeFactor, baseY, minValue, heightFactor) {
		ctx.strokeStyle = this.color;
		ctx.beginPath();

		let first = true;
		this.values.forEach((value) => {
			const posX = (value.timeStampMs - startTimeMs) * timeFactor;
			const posY = baseY - (value.value - minValue) * heightFactor;

			if (first) {
				ctx.moveTo(posX, posY);
				first = false;
			} else {
				ctx.lineTo(posX, posY);
			}
		});

		ctx.stroke();
	}

	/**
	 * Collects series information for the legend.
	 */
	collectSeriesInfo(seriesInfo) {
		seriesInfo.push({
			name: this.name,
			color: this.color,
		});
	}

	/**
	 * Collect values for the specified time. The values are added in the same
	 * order as collectSeriesInfo.
	 */
	collectValues(tstamp, unit, values) {
		const rightBound = this.values.length - 1;
		let leftIdx = 0;
		let rightIdx = rightBound;
		let curIdx;
		let cur = null;

		// Keep any found value (will give a close one)
		while (leftIdx <= rightIdx) {
			curIdx = Math.floor((leftIdx + rightIdx) / 2);

			cur = this.values[curIdx];
			if (cur.timeStampMs < tstamp) {
				leftIdx = curIdx + 1;
			} else if (cur.timeStampMs > tstamp) {
				rightIdx = curIdx - 1;
			} else {
				break;
			}
		}

		// But reject the value if outside of the bounds of the series
		if ((curIdx == 0 && cur.timeStampMs > tstamp) ||
			(curIdx === rightBound && cur.timeStampMs < tstamp)) {
			cur = null;

		}

		if (cur === null) {
			values.push("—");
		} else {
			values.push("" + Math.round(cur.value * 10) / 10 + " " + unit);
		}
	}
}


/**
 * Usable intervals (in minutes) between vertical markers, in increasing
 * order. The smallest one in the list that does not cause values to be too
 * close together will be used.
 * If none of them fit, the graph will draw one marker per N days (N chosen so
 * it fits).
 */
const INTERVALS = [
	15,
	30,
	60,
	120,   // 2 hours
	300,   // 5 hours
	720,   // 12 hours
	1440,  // 1 day
];

const HANDLERS = [
	['zoom-plus', 'ZoomPlus'],
	['zoom-minus', 'ZoomMinus'],
	['left', 'MoveLeft'],
	['right', 'MoveRight'],
	['1d', 'DispDay'],
	['def', 'DispDefault'],
	['1m', 'DispMonth'],
	['1y', 'DispYear'],
	['export', 'Export'],
];
