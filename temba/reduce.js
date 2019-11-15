const data = [{ duration: 10,
             any: 'other fields' },
             { duration: 20,
              any: 'other fields' }
             ];
             
let result = data.reduce((r, d) => r + d.duration, 0);

console.log(result);