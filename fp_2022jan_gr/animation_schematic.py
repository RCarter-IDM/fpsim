'''
Plot animation of agents in FPsim
'''

import os
import numpy as np
import sciris as sc
import pylab as pl
import fpsim as fp
import fp_analyses as fa


n_side = 10
n = n_side**2
filename = 'animation_data.obj'
rerun  = 0
doplot = 1
dosave = 1

if rerun or not os.path.exists(filename):

    data = dict()

    def record(sim):
        entry = sc.objdict()
        entry.i = sim.i
        entry.y = sim.y
        entry.dead     = sc.dcp(~sim.people.alive)
        entry.active   = sc.dcp(sim.people.sexually_active)
        entry.preg     = sc.dcp(sim.people.pregnant)
        entry.method   = sc.dcp(sim.people.method)
        entry.children = sc.dcp(sim.people.children)
        data[sim.i] = entry
        return

    pars = fa.senegal_parameters.make_pars()
    pars['n'] = n
    pars['start_year'] = 1990
    pars['interventions'] = [record]
    sim = fp.Sim(pars)
    sim.run()

    sim.plot()

    sc.save(filename, data)

else:

    data = sc.load(filename)


#%% Plot

if doplot:

    sc.options(font='Avenir')

    cmap = sc.objdict(
        inactive = '#000',
        active   = '#0a0',
        preg     = '#ff0',
        method   = '#f00',
        dead     = '#ccc',
    )

    cnames = sc.objdict(
        inactive = 'Not sexually\nactive',
        active   = 'Sexually\nactive',
        preg     = 'Pregnant',
        method   = 'Using\ncontraception',
        dead     = 'Dead',
    )

    max_children = 9
    max_method = 10
    radius = 0.2
    mothersize = 100

    x = np.arange(n_side)
    y = np.arange(n_side)
    xx,yy = np.meshgrid(x,y)
    xx = xx.flatten()
    yy = yy.flatten()

    sc.options(dpi=200)
    fig,ax = pl.subplots(figsize=(7.5,6))
    δ = 0.5
    pl.axis('off')
    pl.xlim((-δ, n_side-1+δ))
    pl.ylim((-δ, n_side-1+δ))
    sc.figlayout(top=0.93, right=0.72)

    frames = []

    stride = 2 # Don't render every frame
    with sc.timer('generating'):
        print('Generating...')
        for entry in list(data.values())[::stride]:
            print(f'  Working on {entry.i} of {len(data)}...')
            frame = sc.autolist()
            if not dosave:
                pl.cla()

            # Handle counts
            counts = sc.objdict()
            cc = np.array([cmap.inactive]*n, dtype=object)
            colorkeys = ['dead', 'active', 'preg', 'method']
            for key in colorkeys:
                inds = sc.findinds(entry[key][:n])
                cc[inds] = cmap[key]
                counts[key] = len(inds)
            counts['inactive'] = n - counts[:].sum()

            # Plot legend
            for key,label in cnames.items():
                pl.scatter(np.nan, np.nan, s=mothersize, c=cmap[key], label=f'{label} ({counts[key]})')
            frame += ax.legend(loc=(1.05,0.7))

            # Actually plot
            frame += pl.scatter(xx, yy, s=mothersize, c=cc)
            for m, (ix,iy) in enumerate(zip(xx,yy)):
                n_children = len(entry.children[m])
                if n_children:
                    c_arr = np.arange(n_children)
                    rad = 2*np.pi*c_arr/max_children
                    dx = radius*np.cos(rad)
                    dy = radius*np.sin(rad)
                    frame += pl.scatter(ix+dx, iy+dy, s=10, c='k')

            kwargs = dict(transform=pl.gca().transAxes, horizontalalignment='center', fontweight='bold') # Set the "title" properties
            frame += pl.text(0.5, 1.02, f'Year: {entry.y:0.1f}', **kwargs) # Unfortunately pl.title() can't be dynamically updated
            frames.append(frame)
            pl.xlim((-δ, n_side-1+δ))
            pl.ylim((-δ, n_side-1+δ))
            pl.axis('off')
            if not dosave:
                pl.pause(0.01)

    if dosave:
        with sc.timer('saving'):
            print('Saving...')
            sc.savemovie(frames, 'fp_animation_schematic.mp4', fps=10, quality='high') # Save movie as a high-quality mp4

print('Done.')
