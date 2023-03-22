'''
Simple example usage for FPsim
'''

import fpsim as fp

# Set options
do_plot = True

# Run
exp = fp.Experiment()
exp.run()

df = exp.summarize()
print(df)

if do_plot:
    exp.sim.plot()
    exp.plot()
    exp.fit.plot()

print('Done.')


