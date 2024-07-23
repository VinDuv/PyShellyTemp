/* Graph canvas implementation */
/* jshint esversion: 6 */
/* exported LineGraph, Axis, Series */

/**
 * Defines an axis of the graph. Can be set to either the left or right side
 * or the graph. Also associated with a Data to scale it properly when it is
 * drawn.
 */
class Axis {
	constructor(title, color, min, max) {
		this.title = title;
		this.color = color;
		this.min = min;
		this.max = max;
	}

	/**
	 * Layouts the axis. Takes the context, the total canvas height, the axis
	 * label font, the axis value font, and returns an object containing:
	 * x, y: coordinates to pass to drawLabel in order to draw the label
	 * centered, but on the leftmost position. To shift the label to the
	 * right, increase *y* (this is counter-intuitive but that is because the
	 * label is drawn rotated)
	 * labelWidth: The width occupied by the label (in normal canvas
	 * coordinates)
	 * valueMaxWidth: The maximum width occupied by a value
	 */
	getAxisLayoutInfo(ctx, height, axisLabelFont, axisValueFont) {
		ctx.font = axisLabelFont;
		const lMetrics = ctx.measureText(this.title);

		const labelOffset = Math.floor((height - lMetrics.width) / 2);
		const labelTextHeight = Math.ceil(lMetrics.actualBoundingBoxAscent +
			lMetrics.actualBoundingBoxDescent);

		ctx.font = axisValueFont;
		const minValueWidth = ctx.measureText('' + this.min).width;
		const maxValueWidth = ctx.measureText('' + this.max).width;
		const valueMaxWidth = Math.ceil(Math.max(minValueWidth, maxValueWidth));

		return {
			x: -height + labelOffset,
			y: lMetrics.actualBoundingBoxAscent,
			labelWidth: labelTextHeight,
			valueMaxWidth: valueMaxWidth,
		};
	}

	/**
	 * Draws the label text at the specified coordinates with the specified
	 * font.
	 */
	 drawLabel(ctx, font, pos) {
		ctx.save();
		ctx.fillStyle = this.color;
		ctx.font = font;
		ctx.rotate(-Math.PI / 2);
		ctx.fillText(this.title, pos.x, pos.y);
		ctx.restore();
	 }
}


/**
 * Value series.
 * Constructed from a name, a color, an axis, and an array of
 * {timeStampMs: …, value: …} objects.
 */
class Series {
	constructor(name, color, axis, values) {
		this.name = name;
		this.color = color;
		this.axis = axis;
		this.values = values;
	}

	/**
	 * Draw the series in the provided context.
	 */
	draw(ctx, startTimeMs, endTimeMs, startX, endX, height) {
		const axis = this.axis;
		const timeFactor = (endX - startX) / (endTimeMs - startTimeMs);
		const heightFactor = height / (axis.max - axis.min);

		ctx.strokeStyle = this.color;
		ctx.beginPath();

		let first = true;
		this.values.forEach((value) => {
			const posX = startX + (value.timeStampMs - startTimeMs) *
				timeFactor;
			const posY = height - (value.value - axis.min) * heightFactor;

			if (first) {
				ctx.moveTo(posX, posY);
				first = false;
			} else {
				ctx.lineTo(posX, posY);
			}
		});

		ctx.stroke();
	}
}


/**
 * Main class
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
		this.gridColor = '#999999';
		this.textColor = '#000000';
		this.marginPx = 5;
		this.horizLines = 20;
		this.horizValueInterval = 5;
		this.timeValueSpacingPx = 10;

		// Time range
		this.endTimeMs = Date.now();
		this.startTimeMs = this.endTimeMs - 3 * 86400 * 1000;

		// Series
		this.series = [];

		// Axis configuration
		this.leftAxis = null;
		this.rightAxis = null;

		// Delegate
		this.delegate = null;

		// Private attributes
		this._context = ctx;
		this._width = canvas.width;
		this._height = canvas.height;

		this._leftAxisInfo = null;
		this._rightAxisInfo = null;
		this._bottomAxisInfo = null;
		this._legendInfo = null;
		this._horizLineInterval = null;
		this._canvasScrollInfo = null;
		this._busy = false;

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
	 * Should be called at startup, after the delegate has been set, to trigger
	 * the initial data load.
	 */
	initLoad() {
		this._notifyRangeCallback();
	}

	/**
	 * Fully refreshes the graph after an axis and/or data change.
	 */
	refresh() {
		const ctx = this._context;
		ctx.textBaseline = 'alphabetic';

		this._busy = false;

		let leftAxisInfo;
		let rightAxisInfo;

		// Layout the left axis (if any) and the left part of the screen
		if (this.leftAxis === null) {
			leftAxisInfo = {
				labelWidth: 0,
				valueMaxWidth: 0,
				graphX: 0,
			};
		} else {
			leftAxisInfo = this.leftAxis.getAxisLayoutInfo(ctx,
				this._height, this.axisLabelFont, this.axisValueFont);
		}

		if (leftAxisInfo.labelWidth > 0) {
			leftAxisInfo.valueX = leftAxisInfo.labelWidth + this.marginPx;
		} else {
			leftAxisInfo.valueX = 0;
		}

		if (leftAxisInfo.valueMaxWidth > 0) {
			leftAxisInfo.valueX += leftAxisInfo.valueMaxWidth;
			leftAxisInfo.graphX = leftAxisInfo.valueX + this.marginPx;
			leftAxisInfo.valueIncrement = (this.leftAxis.max -
				this.leftAxis.min) * this.horizValueInterval / this.horizLines;
		} else {
			leftAxisInfo.graphX = leftAxisInfo.valueX;
			leftAxisInfo.valueIncrement = null;
		}

		// Do the same for the right axis
		if (this.rightAxis === null) {
			rightAxisInfo = {
				labelWidth: 0,
				valueMaxWidth: 0,
				graphX: this._width,
			};
		} else {
			rightAxisInfo = this.rightAxis.getAxisLayoutInfo(ctx,
				this._height, this.axisLabelFont, this.axisValueFont);
		}

		if (rightAxisInfo.labelWidth > 0) {
			const labelX = this._width - rightAxisInfo.labelWidth;
			rightAxisInfo.y += labelX;
			rightAxisInfo.valueX = labelX - this.marginPx;
		} else {
			rightAxisInfo.valueX = this._width;
		}

		if (rightAxisInfo.valueMaxWidth > 0) {
			rightAxisInfo.valueX -= rightAxisInfo.valueMaxWidth;
			rightAxisInfo.graphX = rightAxisInfo.valueX - this.marginPx;
			rightAxisInfo.valueIncrement = (this.rightAxis.max -
				this.rightAxis.min) * this.horizValueInterval / this.horizLines;
		} else {
			rightAxisInfo.valueIncrement = null;
		}

		// Layout the bottom part of the screen
		ctx.font = this.axisValueFont;
		// Bottom labels can display either a year, MM/DD, or HH:MM;
		// XX/XX is slightly larger
		const metrics = ctx.measureText('XX/XX');

		// The text will be aligned with the vertical lines so it may need
		// some space on the edges of the graph
		const reqMargin = Math.ceil(metrics.width / 2);
		if (leftAxisInfo.graphX < reqMargin) {
			leftAxisInfo.graphX = reqMargin;
		}

		if (rightAxisInfo.graphX > (this._width - reqMargin)) {
			rightAxisInfo.graphX = (this._width - reqMargin);
		}

		const valueTextHeight = Math.ceil(metrics.actualBoundingBoxAscent +
			metrics.actualBoundingBoxDescent);

		let bottomAxisInfo = {
			graphY: this._height - valueTextHeight - this.marginPx,
			horizValY: this._height - Math.ceil(valueTextHeight / 2) -
				this.marginPx,
			valueY: this._height - Math.ceil(metrics.actualBoundingBoxDescent),
			valueWidth: metrics.width,
			graphWidth: rightAxisInfo.graphX - leftAxisInfo.graphX,
			graphTimeIntervalMs: 0,
		};

		// Layout the horizontal lines and the associated value labels
		let topSpacing;
		if (this.leftAxis === null && this.rightAxis == null) {
			topSpacing = 1;
		} else {
			topSpacing = Math.ceil(metrics.actualBoundingBoxAscent);
		}

		this._horizLineInterval = Math.floor((this._height - topSpacing) /
			this.horizLines);

		// Layout the legend
		let legendInfo = {
			maxTextWidth: 0,
			maxTextHeight: 0,
			maxColors: 0,
			items: [],
		};

		let itemsDict = {};
		ctx.font = this.legendFont;
		this.series.forEach((series) => {
			let seriesColors = itemsDict[series.name];
			if (seriesColors === undefined) {
				seriesColors = [];
				legendInfo.items.push({
					name: series.name,
					colors: seriesColors,
				});
				itemsDict[series.name] = seriesColors;
			}
			const colorCount = seriesColors.unshift(series.color);

			const textMetric = ctx.measureText(series.name);
			const textWidth = Math.ceil(textMetric.width);
			const textHeight = Math.ceil(textMetric.fontBoundingBoxAscent +
				textMetric.fontBoundingBoxDescent);

			if (legendInfo.maxTextWidth < textWidth) {
				legendInfo.maxTextWidth = textWidth;
			}

			if (legendInfo.maxTextHeight < textHeight) {
				legendInfo.maxTextHeight = textHeight;
			}

			if (legendInfo.maxColors < colorCount) {
				legendInfo.maxColors = colorCount;
			}
		});

		if (legendInfo.maxColors > 0) {
			legendInfo.width = 2 + 2 * this.marginPx +
				(legendInfo.maxTextHeight + this.marginPx) *
				legendInfo.maxColors + legendInfo.maxTextWidth;
			legendInfo.height = 2 + this.marginPx + (legendInfo.maxTextHeight +
				this.marginPx) * legendInfo.items.length;
		}

		this._leftAxisInfo = leftAxisInfo;
		this._rightAxisInfo = rightAxisInfo;
		this._bottomAxisInfo = bottomAxisInfo;
		this._legendInfo = legendInfo;

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
		const textHeight = Math.ceil(metrics.fontBoundingBoxAscent +
			metrics.fontBoundingBoxDescent);

		ctx.fillStyle = 'white';
		ctx.fillRect(Math.floor(centerX - textWidth / 2), Math.floor(centerY -
			textHeight / 2), textWidth, textHeight);

		ctx.fillStyle = this.textColor;
		ctx.fillText(message, centerX, centerY);

		this._busy = true;
	}

	/**
	 * Property indicating if the graph has been marked as busy.
	 */
	get busy() {
		return this._busy;
	}

	_recalcTimeMarkersAndRedraw() {
		const bottomAxisInfo = this._bottomAxisInfo;
		const timeIntervalMs = this.endTimeMs - this.startTimeMs;
		let intervalMs;

		if (timeIntervalMs !== bottomAxisInfo.graphTimeIntervalMs) {
			// Recalculate the interval between vertical time markers
			const minMarkerSpacing = Math.ceil(bottomAxisInfo.valueWidth +
				this.timeValueSpacingPx);
			const maxTimeMarkers = Math.floor(this._width / minMarkerSpacing);
			const secsBetweenMarkers = Math.floor(timeIntervalMs / 1000 /
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

			let markerHighLight;
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

			bottomAxisInfo.tickIntervalMs = intervalMs;
			bottomAxisInfo.graphTimeIntervalMs = timeIntervalMs;
			bottomAxisInfo.markerHighLight = markerHighLight;
		} else {
			intervalMs = bottomAxisInfo.tickIntervalMs;
		}

		// Reposition the markers
		const graphWidth = bottomAxisInfo.graphWidth;
		const firstMarkerTime = Math.ceil(this.startTimeMs / intervalMs) *
			intervalMs;
		const firstMarkerX = (firstMarkerTime - this.startTimeMs) *
			graphWidth / timeIntervalMs;
		const markerSpacing = intervalMs * graphWidth / timeIntervalMs;

		bottomAxisInfo.initTime = new Date(firstMarkerTime);
		bottomAxisInfo.initX = firstMarkerX;
		bottomAxisInfo.intervalWidth = markerSpacing;

		this._redraw();
	}

	_redraw() {
		const ctx = this._context;
		const bottomAxisInfo = this._bottomAxisInfo;
		ctx.lineWidth = 1.0;
		ctx.textAlign = 'start';
		ctx.textBaseline = 'alphabetic';
		ctx.fillStyle = 'white';
		ctx.fillRect(0, 0, this._width, this._height);

		if (this.leftAxis !== null) {
			this.leftAxis.drawLabel(ctx, this.axisLabelFont,
				this._leftAxisInfo);
		}

		if (this.rightAxis !== null) {
			this.rightAxis.drawLabel(ctx, this.axisLabelFont,
				this._rightAxisInfo);
		}

		ctx.font = this.axisValueFont;
		ctx.strokeStyle = this.gridColor;
		ctx.fillStyle = this.textColor;

		const graphLeftX = this._leftAxisInfo.graphX;
		const graphRightX = this._rightAxisInfo.graphX;
		let curLineY = bottomAxisInfo.graphY + 0.5;

		ctx.beginPath();
		for (let i = 0 ; i <= this.horizLines ; i += 1) {
			ctx.moveTo(graphLeftX, curLineY);
			ctx.lineTo(graphRightX, curLineY);
			curLineY -= this._horizLineInterval;
		}

		ctx.stroke();

		// Used later
		const topY = curLineY + this._horizLineInterval - 0.5;

		const valueCount = Math.floor(this.horizLines /
			this.horizValueInterval);
		const valueInterval = this._horizLineInterval * this.horizValueInterval;
		let leftValue = this.leftAxis === null ? null : this.leftAxis.min;
		let rightValue = this.rightAxis === null ? null : this.rightAxis.min;
		let curTextY = bottomAxisInfo.horizValY;

		for (let i = 0 ;  i <= valueCount ; i += 1) {
			if (leftValue !== null) {
				ctx.textAlign = 'end';
				ctx.fillText('' + Math.round(leftValue),
					this._leftAxisInfo.valueX, curTextY);
				leftValue += this._leftAxisInfo.valueIncrement;
			}

			if (rightValue !== null) {
				ctx.textAlign = 'start';
				ctx.fillText('' + Math.round(rightValue),
					this._rightAxisInfo.valueX, curTextY);
				rightValue += this._rightAxisInfo.valueIncrement;
			}

			curTextY -= valueInterval;
		}

		const intervalMs = bottomAxisInfo.tickIntervalMs;
		const intervalWidth = bottomAxisInfo.intervalWidth;
		const textY = bottomAxisInfo.valueY;
		const bottomY = bottomAxisInfo.graphY;
		const markerHighLight = bottomAxisInfo.markerHighLight;

		const startTime = new Date(this.startTimeMs);
		let prevYear = startTime.getFullYear();
		let prevMonth = startTime.getMonth();
		let prevDay = startTime.getDate();

		let curTime = bottomAxisInfo.initTime;
		let curX = graphLeftX + bottomAxisInfo.initX;

		ctx.textAlign = 'center';

		ctx.beginPath();
		while (curX <= graphRightX) {
			const curYear = curTime.getFullYear();
			const curMonth = curTime.getMonth();
			const curDay = curTime.getDate();
			let label;
			let highlight;

			if (curYear != prevYear) {
				label = '' + curYear;
				highlight = markerHighLight.year;
			} else if (curMonth != prevMonth) {
				label = LineGraph.ZeroPad(curDay) + '/' +
					LineGraph.ZeroPad(curMonth);
				highlight = markerHighLight.month;
			} else if (curDay != prevDay) {
				label = LineGraph.ZeroPad(curDay) + '/' +
					LineGraph.ZeroPad(curMonth);
				highlight = markerHighLight.day;
			} else {
				label = LineGraph.ZeroPad(curTime.getHours()) + ':' +
					LineGraph.ZeroPad(curTime.getMinutes());
				highlight = false;
			}

			const drawX = Math.round(curX) + 0.5;
			ctx.moveTo(drawX, bottomY);
			ctx.lineTo(drawX, topY);

			if (highlight) {
				ctx.font = 'bold ' + this.axisValueFont;
			} else {
				ctx.font = this.axisValueFont;
			}

			ctx.fillText(label, drawX, textY);

			curTime = new Date(curTime.getTime() + intervalMs);
			curX += intervalWidth;
			prevYear = curYear;
			prevMonth = curMonth;
			prevDay = curDay;
		}

		ctx.stroke();

		ctx.textAlign = 'start';

		ctx.lineWidth = 2;
		ctx.save();
		ctx.rect(graphLeftX, 0, graphRightX - graphLeftX, bottomY);
		ctx.clip();
		this.series.forEach((series) => {
			series.draw(ctx, this.startTimeMs, this.endTimeMs, graphLeftX,
				graphRightX, bottomY);
		});
		ctx.restore();

		// Draw the legend
		const legendInfo = this._legendInfo;

		if (legendInfo.maxColors > 0) {
			const legendX = graphRightX - legendInfo.width - this.marginPx;
			const legendY = bottomAxisInfo.graphY - legendInfo.height -
				this.marginPx;

			ctx.globalAlpha = 0.8;

			ctx.font = this.legendFont;
			ctx.lineWidth = 1;
			ctx.strokeStyle = '#000000';
			ctx.fillStyle = '#FFFFFF';
			ctx.fillRect(legendX, legendY, legendInfo.width, legendInfo.height);
			ctx.strokeRect(legendX + 0.5, legendY + 0.5, legendInfo.width,
				legendInfo.height);

			const colorCellX = legendX + 1 + this.marginPx;
			const colorCellSize = legendInfo.maxTextHeight;
			const colorCellSpacing = colorCellSize + this.marginPx;

			let posY = legendY + 1 + this.marginPx;

			legendInfo.items.forEach((item) => {
				let posX = colorCellX;
				item.colors.forEach((color) => {
					ctx.fillStyle = color;
					ctx.fillRect(posX, posY, colorCellSize, colorCellSize);
					ctx.strokeRect(posX + 0.5, posY + 0.5, colorCellSize,
						colorCellSize);
					posX += colorCellSpacing;
				});

				ctx.textBaseline = 'top';
				ctx.fillStyle = '#000000';
				ctx.fillText(item.name, posX, posY);

				posY += colorCellSpacing;
			});

			ctx.globalAlpha = 1;
		}
	}

	_notifyRangeCallback() {
		if (this.delegate === null) {
			return;
		}

		// Load extra data from the margins so the the graph line start at least
		// at the edge of the graph
		const extraTime = Math.floor((this.endTimeMs - this.startTimeMs) *
			0.05);

		this.delegate.rangeUpdated(this.startTimeMs - extraTime,
			this.endTimeMs + extraTime);
	}

	_handleMouseDown(evt) {
		if (evt.offsetX < this._leftAxisInfo.graphX ||
			evt.offsetX > this._rightAxisInfo.graphX ||
			evt.offsetY > this._bottomAxisInfo.graphY ||
			this._busy) {
			return;
		}

		const scrollInfo = {
			origX: evt.clientX,
			origEnd: this.endTimeMs,
			limitEnd: Date.now(),
			timeInterval: this._bottomAxisInfo.graphTimeIntervalMs,
			curDelta: 0,
			moveHandler: (evt) => {
				this._handleMouseMove(evt);
			},
			upHandler: (evt) =>  {
				this._handleMouseUp(evt);
			},
		};

		window.addEventListener('mousemove', scrollInfo.moveHandler);
		window.addEventListener('mouseup', scrollInfo.upHandler);

		this._canvasScrollInfo = scrollInfo;
	}

	_handleMouseMove(evt) {
		const scrollInfo = this._canvasScrollInfo;
		const delta = evt.clientX - scrollInfo.origX;

		if (delta === scrollInfo.curDelta) {
			return;
		}

		scrollInfo.curDelta = delta;

		const graphWidth = this._bottomAxisInfo.graphWidth;
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

		this._notifyRangeCallback();
	}

	_handleZoomPlus() {
		const curIntervalMs = this._bottomAxisInfo.graphTimeIntervalMs;
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
		const curIntervalMs = this._bottomAxisInfo.graphTimeIntervalMs;
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
		const curIntervalMs = this._bottomAxisInfo.graphTimeIntervalMs;
		const delta = Math.round(curIntervalMs * 3 / 4);

		this.endTimeMs -= delta;
		this.startTimeMs -= delta;

		this._recalcTimeMarkersAndRedraw();
		this._notifyRangeCallback();
	}

	_handleMoveRight() {
		const curIntervalMs = this._bottomAxisInfo.graphTimeIntervalMs;
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

LineGraph.ZeroPad = function (value) {
	if (value < 10) {
		return '0' + value;
	}

	return '' + value;
};

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
	['1m', 'DispMonth'],
	['1y', 'DispYear'],
	['export', 'Export'],
];
