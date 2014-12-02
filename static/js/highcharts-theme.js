Highcharts.theme = {
   chart: {
      borderWidth: 0,
      borderRadius: 0,
      plotShadow: false,
      plotBorderWidth: 0
   },
   title: {
      style: {
         color: '#FFF',
         font: '16px Lucida Grande, Lucida Sans Unicode, Verdana, Arial, Helvetica, sans-serif'
      }
   },
   subtitle: {
      style: {
         color: '#DDD',
         font: '12px Lucida Grande, Lucida Sans Unicode, Verdana, Arial, Helvetica, sans-serif'
      }
   },
   xAxis: {
      gridLineWidth: 0,
      lineColor: 'rgba(0, 0, 0, .03)',
      tickColor: 'rgba(0, 0, 0, .03)',
      labels: {
         style: {
            color: '#999',
            fontWeight: 'bold'
         }
      },
      title: {
         style: {
            color: '#AAA',
            font: 'bold 12px Lucida Grande, Lucida Sans Unicode, Verdana, Arial, Helvetica, sans-serif'
         }
      }
   },
   yAxis: {
      alternateGridColor: null,
      minorTickInterval: null,
      tickColor: 'yellow',
      gridLineColor: 'rgba(0, 0, 0, .03)',
      lineColor:'transparent',
      lineWidth: 1,
      tickWidth: 0,
      labels: {
         style: {
            color: '#ccc',
            fontWeight: 'bold'
         }
      },
      title: {
         style: {
            color: 'blue',
            font: 'bold 12px Lucida Grande, Lucida Sans Unicode, Verdana, Arial, Helvetica, sans-serif'
         }
      }
   },
   legend: {
      itemStyle: {
         color: '#CCC'
      },
      itemHoverStyle: {
         color: '#FFF'
      },
      itemHiddenStyle: {
         color: '#333'
      }
   },
   labels: {
      style: {
         color: '#CCC'
      }
   },
   tooltip: {
      borderRadius:0,
      shadow: 0,
      borderWidth: 1,
   },

   plotOptions: {
      series: {
         shadow: 0
      },
      line: {
         dataLabels: {
            color: '#CCC'
         },
         marker: {
            lineColor: '#333'
         }
      },
      spline: {
         marker: {
            lineColor: '#333'
         }
      },
      scatter: {
         marker: {
            lineColor: '#333'
         }
      },
      candlestick: {
         lineColor: 'white'
      }
   },

   toolbar: {
      itemStyle: {
         color: '#CCC'
      }
   },
   navigator: {
      handles: {
         backgroundColor: '#666',
         borderColor: '#AAA'
      },
      outlineColor: '#CCC',
      maskFill: 'rgba(16, 16, 16, 0.5)',
      series: {
         color: '#7798BF',
         lineColor: '#A6C7ED'
      }
   }
};

Highcharts.setOptions(Highcharts.theme);