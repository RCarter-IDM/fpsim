'''
Defines the Sim class, the core class of the FP model (LEMOD-FP).
'''

#%% Imports
import numpy as np # Needed for a few things not provided by pl
import pylab as pl
import sciris as sc
from scipy import interpolate as si
import pandas as pd
from . import population
from . import utils
from . import base


# Specify all externally visible things this file defines
__all__ = ['Person', 'Sim', 'multi_run']

useSI      = True
mpy        = 12 # Months per year, to avoid magic numbers
resolution = 100 # For spline interpolation
eps = 1e-12


#%% Define classes


class Person(base.ParsObj):
    '''
    Class for a single person.
    '''
    def __init__(self, pars, age=0, sex=0, parity=0, method=None, barrier='None', breastfeed_dur = 0, breastfeed_dur_total = 0, postpartum_dur = 0, unmet=False, mortality_fn=None, fertility_fn=None, mortality_trend=None):
        self.update_pars(pars) # Set parameters
        self.uid = str(pl.randint(0,1e9))
        self.age = float(age) # Age of the person (in years)
        self.sex = sex # Female (0) or male (1)
        self.parity = parity
        self.method = method
        self.barrier = barrier
        self.unmet = unmet
        self.alive = True
        self.pregnant = False
        self.sexually_active = False
        self.lactating = False
        self.postpartum = False
        self.postpartum_dur = postpartum_dur
        self.postpartum_infecund = False
        self.breastfeed_dur = breastfeed_dur
        self.breastfeed_dur_total = breastfeed_dur_total
        self.dobs = [-1]*self.parity # Dates of births
        self.mortality_fn = mortality_fn
        f_var = self.pars['fertility_variation']
        personal_fertility = np.random.random()*(f_var[1]-f_var[0])+f_var[0] # Stretch fertility by a factor bounded by [f_var[0], f_var[1]]
        self.fertility_fn = fertility_fn*personal_fertility
        self.mortality_trend = mortality_trend
        #self.fertility_trend = fertility_trend

        self.init_step_results() # Do I need this?
        return


    # def get_mortality_prob(self): # Slow -- takes 36% of the time
    #     mortality_eval = self.mortality_fn[int(round(self.age*resolution))]
    #     prob = mortality_eval*self.pars['mortality_factor']/mpy
    #     return prob


    # def get_preg_prob(self): # Slow -- see numba_preg_prob
    #     preg_eval = self.fertility_fn[int(round(self.age*resolution))]
    #     method_eff = self.pars['method_efficacy'][self.method]
    #     prob = method_eff*preg_eval*self.pars['fertility_factor']/mpy
    #     return prob


    def get_method(self):
        '''
        Uses a switching matrix to decide based on a person's original method their probability of changing to a
        new method and assigns them the new method. Currently allows switching on whole calendar years
        '''
        orig_method = self.method

        if self.age < 18:
            matrix = self.pars['methods']['<18']
        elif 18 < self.age <= 20:
            matrix = self.pars['methods']['18-20']
        elif 20 < self.age <= 25:
            matrix = self.pars['methods']['21-25']
        else:
            matrix = self.pars['methods']['>25']

        #matrix = self.pars['methods']['switch_general']   Use if going back to non-age stratified
        choices = matrix[orig_method]

        new_method = utils.mt(choices)
        self.method = new_method

        if self.pars['verbose']>1:
            if orig_method != 0 or new_method != 0: # Assume 0 = None
                print(f'Switched from {orig_method} to {new_method}')
        return

    def get_method_postpartum(self):
        '''Utilizes data from birth to allow agent to pick a method postpartum and then
        continue or discontinue by 6 months postpartum.  Next opportunity to switch
        methods will be on whole calendar years, whenever that falls'''

        if 0 <= self.postpartum_dur <= 3: # From 0 - 3 months postpartum
            choices_from_birth = self.pars['methods_postpartum']['start_postpartum']
            self.method = utils.mt(choices_from_birth)

        if 3 < self.postpartum_dur <=6:  # From 4-6 months postpartum
            orig_method = self.method
            choices_postpartum = self.pars['methods_postpartum']['switch_postpartum'][orig_method]
            self.method = utils.mt(choices_postpartum)

        return

    def check_mortality(self, t):
        '''Decide if person dies at a timestep'''
        # mortality_prob = self.get_mortality_prob()
        mortality_prob = utils.numba_mortality_prob(t, self.mortality_fn, self.mortality_trend, self.age, resolution, mpy)

        died = utils.bt(mortality_prob)
        if died:
            self.alive = False
            self.step_results['died'] = True

        return



    def check_sexually_active(self):
        '''
        Decide if agent is sexually active based either on month postpartum or age if
        not postpartum.  Postpartum and general age data from DHS.
        '''

        # Check if postpartum in first year and assign probability
        if self.postpartum and 0 < self.postpartum_dur < 12 :
            sexually_active_prob = self.pars['sexual_activity_postpartum']['percent_active'][self.postpartum_dur]

        # Else check if sexually active in the last year given their age - from DHS parameters
        else:
            sexually_active_prob = utils.numba_activity_prob(self.pars['sexual_activity'], self.age,
                                                             resolution)

        # Evaluate likelihood in this timestep of being sexually active
        sexually_active = utils.bt(sexually_active_prob)

        if sexually_active:
            self.sexually_active = True
        else:
            self.sexually_active = False

        return


    def check_conception(self, t):
        '''
        Decide if person (female) becomes pregnant at a timestep.
        '''
        # preg_prob = self.get_preg_prob()

        # Find monthly probability of pregnancy based on fecundity and any use of contraception - from data
        preg_prob = utils.numba_preg_prob(self.fertility_fn, self.age, resolution,
                                          self.pars['method_efficacy'][self.method], mpy )

        # Adjust for decreased likelihood of conception if nulliparous vs already gravid - from data
        if self.parity == 0:   #Nulliparous
            ind = sc.findnearest(self.pars['fecundity_ratio_nullip'][0], self.age)
            fr_nullip = self.pars['fecundity_ratio_nullip'][1][ind]
            preg_prob *= fr_nullip

        # Adjust for postpartum infecundability - parameters are women meeting criteria for lactational amenorrhea.
        # Postpartum abstinence accounted for in check_sexually_active function
        if self.postpartum and 0 <= self.postpartum_dur < 12:
            prob_postpartum_infecund = self.pars['lactational_amenorrhea']['percent'][self.postpartum_dur]
            preg_prob *= (1 - prob_postpartum_infecund)

        # Adjust for probability of exposure to pregnancy episode at this timestep based on age and parity - encapsulates background factors - experimental and tunable
        ind = sc.findnearest(self.pars['exposure_correction_age'][0], self.age)
        exposure_range = self.pars['exposure_correction_age'][1][ind]
        exposure_correction_age = np.random.uniform(exposure_range[0], exposure_range[1]) # Affect exposure by a random number between interval for agent's age
        ind2 = sc.findnearest(self.pars['exposure_correction_parity'][0], self.parity)
        exposure_correction_parity = self.pars['exposure_correction_parity'][1][ind2]

        preg_prob *= exposure_correction_age
        preg_prob *= exposure_correction_parity

        # Use a single binomial trial to check for conception successes this month
        pregnant = utils.bt(preg_prob)

        if pregnant:
            abortion = utils.bt(self.pars['abortion_prob']) # Check to see if this pregnancy ends in abortion, do not set up pregnancy if yes
            if abortion:
                self.postpartum = False
                self.postpartum_dur = 0  # Reset postpartum state if got pregnant and then aborted
                return

            self.pregnant = True
            #self.method = 0  # if commented out, postpartum women will go back to whatever they were using pre-conception as their original method
            self.gestation = 0  # Start the counter at 0 to allow full 9 months gestation
            self.preg_dur = (np.random.randint(self.pars['preg_dur'][0], self.pars['preg_dur'][1] + 1))  # Duration of this pregnancy
            self.postpartum = False
            self.postpartum_dur = 0
            self.reset_breastfeeding() # Stop lactating if becoming pregnant

        return

    def update_breastfeeding(self):
        '''Track breastfeeding, and update time of breastfeeding for individual pregnancy'''
        if self.breastfeed_dur >= (np.random.randint(self.pars['breastfeeding_dur'][0],
                                                             self.pars['breastfeeding_dur'][
                                                                 1] + 1)):  # when each breastfeeding episode terminates (in months) within range set in parameters
            self.reset_breastfeeding()

        else:
            self.breastfeed_dur += self.pars['timestep']

        return


    def update_postpartum(self):
        '''Track duration of extended postpartum period (0-24 months after birth)'''
        '''Should only enter this function if postpartum state on agent is positive'''

        # Stop postpartum episode if reach max length (set to 24 months)
        if self.postpartum_dur >= (self.pars['postpartum_length']):
            self.postpartum = False
            self.postpartum_dur = 0

        # Count the state of the agent
        if self.postpartum:
            if 0 <= self.postpartum_dur < 6:
                self.step_results['pp0to5'] = True
            elif 6 <= self.postpartum_dur < 12:
                self.step_results['pp6to11'] = True
            elif 12 <= self.postpartum_dur < 24:
                self.step_results['pp12to23'] = True

            # Advance the state to next timestep
            self.postpartum_dur += self.pars['timestep']

        return


    def update_pregnancy(self):
        '''Advance pregnancy in time and check for miscarriage'''

        if self.pregnant:
            self.gestation += self.pars['timestep']

            # Check for probability of miscarriage this pregnancy and end pregnancy if miscarried
            miscarriage_prob = utils.numba_miscarriage_prob(self.pars['miscarriage_rates'], self.age, resolution)

            if self.gestation == (self.pars['end_first_tri']):
                miscarriage = utils.bt(miscarriage_prob)
                if miscarriage:
                    self.pregnant = False
                    self.postpartum = False
                    self.gestation = 0  # Reset gestation counter
        return

    def reset_breastfeeding(self):
        '''Stop breastfeeding, calculate total lifetime duration so far, and reset lactation episode to zero'''
        self.lactating = False
        self.breastfeed_dur_total += self.breastfeed_dur
        self.breastfeed_dur = 0

        return

    def maternal_mortality(self, y):
        '''Check for probability of maternal mortality'''

        ind = sc.findnearest(self.pars['maternal_mortality']['year'], y)
        maternal_death_prob = self.pars['maternal_mortality']['probs'][ind]

        self.step_results['maternal_death'] = utils.bt(maternal_death_prob)

        if self.step_results['maternal_death']:
            self.alive = False
            self.step_results['died'] = True

        return

    def infant_mortality(self, y):
        '''Check for probability of infant mortality (death < 1 year of age)'''

        ind = sc.findnearest(self.pars['infant_mortality']['year'], y)
        infant_mort_prob = self.pars['infant_mortality']['probs'][ind]

        self.step_results['infant_death'] = utils.bt(infant_mort_prob)

        if self.step_results['infant_death']:
            self.step_results['gave_birth'] = False  # Don't count as a new person to add
            self.reset_breastfeeding()

        return

    def check_delivery(self, y):
        '''Decide if pregnant woman gives birth and explore maternal mortality and child mortality'''

        if self.gestation >= self.preg_dur:  # Needs to be separate since this won't exist otherwise
            self.pregnant = False
            self.gestation = 0  # Reset gestation counter
            twins = utils.bt(self.pars['twins_prob'])
            if twins:
                self.step_results['gave_birth'] = 2
                self.parity += 2
                self.dobs.append(self.age) # Used for birth spacing only, only add one baby to dob
            else:
                self.step_results['gave_birth'] = 1
                self.parity += 1
                self.dobs.append(self.age)
            self.lactating = True  # Start lactating at time of birth
            self.postpartum = True # Start postpartum state at time of birth
            self.breastfeed_dur = 0  # Start at 0, will update before leaving timestep in separate function
            self.postpartum_dur = 0

            self.maternal_mortality(y)
            self.infant_mortality(y)

        return

    def age_person(self):
        '''Advance age in the simulation'''
        self.age += self.pars['timestep'] / mpy  # Age the person for the next timestep
        self.age = min(self.age, self.pars['max_age'])
        return

    def update_contraception(self, t):
        '''If eligible (age 15-49 and not pregnant), choose new method or stay with current one'''

        if self.postpartum and self.postpartum_dur <= 6:
            self.get_method_postpartum()

        else:
            if t % (self.pars['switch_frequency']/mpy) == 0:  # If switching frequency in months has passed, allows switching only on whole years
                self.get_method()

        if self.pars['method_age'] <= self.age < self.pars['age_limit_fecundity'] and not self.pregnant:   #Tally for mCPR
            if self.method == 0:  # TODO: Think more carefully about the logic here!
                self.step_results['no_method'] = 1
            else:
                self.step_results['on_method'] = 1

        return

    def init_step_results(self):
        self.step_results = {
            'died' : False,
            'gave_birth' : 0,
            'maternal_death' : False,
            'infant_death' : False,
            'on_method' : False,
            'no_method' : False,
            'pp0to5'   : False,
            'pp6to11'   : False,
            'pp12to23'  : False
        }
        return


    def update(self, t, y):
        ''' Update the person's state for the given timestep.  t is the time in the simulation '''

        self.init_step_results()   # Initialize outputs

        if self.alive:  # Do not move through step if not alive

            self.age_person()  # Age person in units of the timestep
            self.check_mortality(t)  # Decide if person dies at this t in the simulation
            if not self.alive:
                return self.step_results

            if self.sex == 0 and self.age < \
                    self.pars['age_limit_fecundity']:  # If female and age 0-49

                if self.pregnant:
                    self.check_delivery(y)  # Deliver with birth outcomes if reached pregnancy duration
                    self.update_pregnancy()  # Advance gestation in timestep, handle miscarriage
                    if not self.alive:
                        return self.step_results

                if not self.pregnant:
                    if self.pars['method_age'] <= self.age < self.pars['age_limit_fecundity']:
                        self.update_contraception(t)
                    self.check_sexually_active()
                    if self.sexually_active:
                        self.check_conception(t)  # Decide if conceives and initialize gestation counter at 0
                    if self.postpartum:
                        self.update_postpartum() # Updates postpartum counter if postpartum

                if self.lactating:
                    self.update_breastfeeding()


        return self.step_results



class Sim(base.BaseSim):
    '''
    The Sim class handles the running of the simulation
    '''

    def __init__(self, pars=None, ):
        super().__init__(pars) # Initialize and set the parameters as attributes
        utils.set_seed(self.pars['seed'])
        self.init_results()
        self.init_people()
        self.interventions = {}  # dictionary for possible interventions to add to the sim

        return


    def init_results(self):
        resultscols = ['t', 'pop_size', 'births', 'deaths', 'maternal_deaths', 'infant_deaths', 'on_method', 'no_method', 'mcpr', 'pp0to5', 'pp6to11', 'pp12to23', 'nonpostpartum']
        self.results = {}
        for key in resultscols:
            self.results[key] = np.zeros(int(self.npts))
        return

    def get_age_sex(self):
        ''' For an ex nihilo person, figure out if they are male and female, and how old '''
        sex = np.random.random() < self.m_frac  # Pick the sex based on the fraction of men vs. women
        spline = self.f_pop_spline if sex == 0 else self.m_pop_spline  # Pick the male or female population spline
        age = si.splev(np.random.random(), spline)  # Use the spline fit to pick the age
        return age, sex

    def make_person(self, age=None, sex=None, method=None):
        _age, _sex = self.get_age_sex()
        if age is None: age = _age
        if sex is None: sex = _sex
        if method is None: method = 0
        barrier_ind = utils.mt(self.pars['barriers'][:])
        barrier = self.pars['barriers'].keys()[barrier_ind]
        if sex == 0:
            mortality_fn = self.f_mortality_fn
        else:
            mortality_fn = self.m_mortality_fn
        if sex == 0:
            fertility_fn = self.f_fecundity_fn
        else:
            fertility_fn = self.m_fecundity_fn

        person = Person(self.pars, age=age, sex=sex, method=method, barrier=barrier, mortality_fn=mortality_fn,
                        fertility_fn = fertility_fn, mortality_trend=self.mortality_trend)
                        #fertility_trend=self.fertility_trend)  # Create the person

        #person = Person(self.pars, age=age, sex= sex, method=method, barrier=barrier, mortality_fn=mortality_fn,
                        #fecundity_fn = fecundity_fn, mortality_trend=self.mortality_trend)

        return person


    def init_people(self):
        ''' Create the people '''
        self.m_pop_spline, self.f_pop_spline, self.m_frac = population.make_age_sex_splines(self.pars)
        #self.m_fertility_fn, self.f_fertility_fn, self.fertility_trend = population.lookup_fertility_mortality_splines(
            #self.pars, which='age_fertility')
        self.m_fecundity_fn, self.f_fecundity_fn = population.lookup_fecundity_splines(self.pars)
        self.m_mortality_fn, self.f_mortality_fn, self.mortality_trend = population.lookup_fertility_mortality_splines(
            self.pars, which='age_mortality')
        self.people = sc.odict()  # Dictionary for storing the people
        for i in range(int(self.pars['n'])):  # Loop over each person
            person = self.make_person()
            self.people[person.uid] = person  # Save them to the dictionary
        return


    def add_intervention(self, intervention, year):
        '''Allow adding an intervention at the time point corresponding to the year passed in '''
        index = self.year2ind(year)
        self.interventions[index] = intervention
        return


    def update_methods_matrix(self, y):

        switch_general = {}

        '''
        for key, val in self.pars['methods']['probs_matrix'].items():
            switch_general[key] = sc.dcp(val)
            ind = sc.findnearest(self.pars['methods']['mcpr_years'], y)  # Find the closest year to the timestep we are on
            switch_general[key][0, 0] *= self.pars['methods']['trend'][ind]  # Takes into account mCPR during year of sim
            for i in range(len(switch_general[key])):
                switch_general[key][i] = switch_general[key][i, :] / switch_general[key][i,
                                                           :].sum()  # Normalize so probabilities add to 1
            self.pars['methods']['key'] = switch_general[key]
        '''

        switch_general['<18'] = sc.dcp(self.pars['methods']['probs_matrix']['<18'])
        ind = sc.findnearest(self.pars['methods']['mcpr_years'], y)  # Find the closest year to the timestep we are on
        switch_general['<18'][0,0] *= self.pars['methods']['trend'][ind] # Takes into account mCPR during year of sim
        for i in range(len(switch_general['<18'])):
            switch_general['<18'][i] = switch_general['<18'][i,:] / switch_general['<18'][i,:].sum() # Normalize so probabilities add to 1
        self.pars['methods']['<18'] = switch_general['<18']

        switch_general['18-20'] = sc.dcp(self.pars['methods']['probs_matrix']['18-20'])
        ind = sc.findnearest(self.pars['methods']['mcpr_years'],
                                 y)  # Find the closest year to the timestep we are on
        switch_general['18-20'][0, 0] *= self.pars['methods']['trend'][ind]  # Takes into account mCPR during year of sim
        for i in range(len(switch_general['18-20'])):
            switch_general['18-20'][i] = switch_general['18-20'][i, :] / switch_general['18-20'][i,
                                                                        :].sum()  # Normalize so probabilities add to 1
        self.pars['methods']['18-20'] = switch_general['18-20']

        switch_general['21-25'] = sc.dcp(self.pars['methods']['probs_matrix']['21-25'])
        ind = sc.findnearest(self.pars['methods']['mcpr_years'],
                                 y)  # Find the closest year to the timestep we are on
        switch_general['21-25'][0, 0] *= self.pars['methods']['trend'][
                ind]  # Takes into account mCPR during year of sim
        for i in range(len(switch_general['21-25'])):
                switch_general['21-25'][i] = switch_general['21-25'][i, :] / switch_general['21-25'][i,
                                                                            :].sum()  # Normalize so probabilities add to 1
        self.pars['methods']['21-25'] = switch_general['21-25']

        switch_general['>25'] = sc.dcp(self.pars['methods']['probs_matrix']['>25'])
        ind = sc.findnearest(self.pars['methods']['mcpr_years'],
                                 y)  # Find the closest year to the timestep we are on
        switch_general['>25'][0, 0] *= self.pars['methods']['trend'][
                ind]  # Takes into account mCPR during year of sim
        for i in range(len(switch_general['>25'])):
            switch_general['>25'][i] = switch_general['>25'][i, :] / switch_general['>25'][i,
                                                                            :].sum()  # Normalize so probabilities add to 1
        self.pars['methods']['>25'] = switch_general['>25']

        '''
        switch_general = sc.dcp(self.pars['methods']['probs_matrix'])
        ind = sc.findnearest(self.pars['methods']['mcpr_years'], y)  # Find the closest year to the timestep we are on
        switch_general[0, 0] *= self.pars['methods']['trend'][ind]  # Takes into account mCPR during year of sim
        for i in range(len(switch_general)):
            switch_general[i] = switch_general[i, :] / switch_general[i,:].sum()  # Normalize so probabilities add to 1
        self.pars['methods']['switch_general'] = switch_general
        '''

        return

    def update_methods_matrix_postpartum(self, y):

        start_postpartum = sc.dcp(self.pars['methods_postpartum']['probs_matrix_0-3'])
        switch_postpartum = sc.dcp(self.pars['methods_postpartum']['probs_matrix_4-6'])

        ind = sc.findnearest(self.pars['methods_postpartum']['mcpr_years'], y)  # Find the closest year to the timestep we are on

        start_postpartum[0] *= self.pars['methods_postpartum']['trend'][ind]
        switch_postpartum[0,0] *= self.pars['methods_postpartum']['trend'][ind]

        start_postpartum = start_postpartum / start_postpartum.sum()  # Normalize so probs add to 1
        self.pars['methods_postpartum']['start_postpartum'] = start_postpartum   # 1d array for probs coming from birth

        for i in range(len(switch_postpartum)):
            switch_postpartum[i] = switch_postpartum[i,:] / switch_postpartum[i,:].sum() # Normalize so probabilities add to 1
        self.pars['methods_postpartum']['switch_postpartum'] = switch_postpartum  # 10x10 matrix for probs of continuing or discontinuing method by 6 months postpartum

        return

    def run(self, verbose=None):
        ''' Run the simulation '''

        T = sc.tic()

        # Reset settings and results
        if verbose is not None:
            self.pars['verbose'] = verbose
        self.update_pars()
        self.init_results()
        self.init_people() # Actually create the children

        # Validate the parameters
        # default_keys = set(make_pars().keys())
        # sim_keys = set(self.pars.keys())
        # if sim_keys != default_keys:
        #     extra_keys = sim_keys - default_keys
        #     missing_keys = default_keys - sim_keys
        #     extra_str = f' Keys not understood: {extra_keys}' if extra_keys else ''
        #     missing_str = f' Missing keys: {missing_keys}' if missing_keys else ''
        #     errormsg = f'Parameter keys are incorrect!{missing_str}{extra_str}'
        #     raise Exception(errormsg)

        # Main simulation loop
        for i in range(self.npts):  # Range over number of timesteps in simulation (ie, 0 to 261 steps)
            t = self.ind2year(i)  # t is time elapsed in years given how many timesteps have passed (ie, 25.75 years)
            y = self.ind2calendar(i)  # y is calendar year of timestep (ie, 1975.75)
            if self.pars['verbose']>-1:
                if sc.approx(t, int(t), eps=0.01):
                    print(f'  Running {y:0.0f} of {self.pars["end_year"]}...')

            # Update switching and mortality
            self.update_methods_matrix(y)
            self.update_methods_matrix_postpartum(y)

            # Update each person
            deaths = 0
            births = 0
            maternal_deaths = 0
            infant_deaths = 0
            on_methods = 0
            no_methods = 0
            pp0to5 = 0
            pp6to11 = 0
            pp12to23 = 0
            total_women_fecund = 0

            for person in self.people.values():
                step_results = person.update(t, y) # Update and count new cases
                deaths          += step_results['died']
                births          += step_results['gave_birth']
                maternal_deaths += step_results['maternal_death']
                infant_deaths    += step_results['infant_death']
                on_methods      += step_results['on_method']
                no_methods      += step_results['no_method']
                pp0to5          += step_results['pp0to5']
                pp6to11         += step_results['pp6to11']
                pp12to23        += step_results['pp12to23']

                if person.sex == 0 and 15 <= person.age < self.pars['age_limit_fecundity']:
                    total_women_fecund += 1

            if i in self.interventions:
                self.interventions[i](self)


            # Births
            for birth in range(births):
                person = self.make_person(age=0) # Save them to the dictionary
                self.people[person.uid] = person

            self.n = len(self.people) - deaths # Calculate new population size


            percent0to5 = (pp0to5 / total_women_fecund) * 100
            percent6to11 = (pp6to11 / total_women_fecund) * 100
            percent12to23 = (pp12to23 / total_women_fecund) * 100
            nonpostpartum = ((total_women_fecund - pp0to5 - pp6to11 - pp12to23)/total_women_fecund) * 100

            # Store results
            self.results['t'][i]                = self.tvec[i]
            self.results['pop_size'][i]         = self.n
            self.results['births'][i]          = births
            self.results['deaths'][i]          = deaths
            self.results['maternal_deaths'][i] = maternal_deaths
            self.results['infant_deaths'][i]    = infant_deaths
            self.results['on_method'][i]       = on_methods
            self.results['no_method'][i]       = no_methods
            self.results['mcpr'][i]            = on_methods/(on_methods+no_methods)
            self.results['pp0to5'][i]          = percent0to5
            self.results['pp6to11'][i]         = percent6to11
            self.results['pp12to23'][i]           = percent12to23
            self.results['nonpostpartum'][i]      = nonpostpartum


        pop_size = self.results['pop_size'][self.npts - 1]
        print(pop_size)
        total_deaths = pl.cumsum(self.results['deaths'][self.npts-13 : self.npts - 1]) +\
                       pl.cumsum(self.results['infant_deaths'][self.npts-13 : self.npts - 1]) +\
                       pl.cumsum(self.results['maternal_deaths'][self.npts-13 : self.npts - 1])
        print(f'Crude death rate per 1,000 inhabitants: {(total_deaths[-1]/pop_size)*1000}.  Total deaths:  {total_deaths[-1]}')  # Deaths in the last year
        infant_deaths = pl.cumsum(self.results['infant_deaths'][self.npts-13 : self.npts - 1])
        maternal_deaths = pl.cumsum(self.results['maternal_deaths'][self.npts - 13: self.npts - 1])
        births_last_year = pl.cumsum(self.results['births'][self.npts - 13: self.npts - 1])
        print(f'Infant mortality over last year: {infant_deaths}')
        print(f'Total infant death rate in model: {(infant_deaths[-1]/births_last_year[-1])*1000}.  Infant death rate 2015 Senegal: 36.4')
        print(f'Total maternal death rate in model: {(maternal_deaths[-1]/births_last_year[-1])*100000}.  Maternal mortality ratio 2015 Senegal: 315 ')
        print(f'Crude birth rate per 1000 inhabitants in model: {(births_last_year[-1]/pop_size)*1000}.  Crude birth rate Senegal 2018: 34.52 per 1000 inhabitants')
        print(f'Final percent non-postpartum : {nonpostpartum}')

        for person in self.people.values():
            if person.lactating:
                person.reset_breastfeeding()


        elapsed = sc.toc(T, output=True)
        print(f'Run finished for "{self.pars["name"]}" after {elapsed:0.1f} s')
        return self.results

    def store_results(self):

        ''' Returns results dictionary for use in other files'''

        return self.results

    def plot_postpartum(self):

        '''Creates a plot over time of various postpartum states in the model'''

        fig = pl.figure()

        x = self.results['t']
        y1 = self.results['pp0to5']
        y2 = self.results['pp6to11']
        y3 = self.results['pp12to23']
        y4 = self.results['nonpostpartum']

        pl.plot(x, y1, label = 'Postpartum 0-5 months')
        pl.plot(x, y2, label = 'Postpartum 6-11 months')
        pl.plot(x, y3, label = 'Postpartum 12-23 months')
        pl.plot(x, y4, label = 'Non-postpartum')

        pl.xlabel('Year')
        pl.ylabel('Percent')
        pl.legend(loc = 'best')
        pl.title('Percent women age 15-49 in postpartum states', fontweight = 'bold')

        pl.show()

        return


    def store_postpartum(self):

        '''Stores snapshot of who is currently pregnant, their parity, and various
        postpartum states for use in calibration'''

        min_age = 12.5
        max_age = self.pars['age_limit_fecundity']

        people = self.people.values()

        rows = []
        for person in people:
            if person.alive and person.sex == 0 and min_age <= person.age < max_age:
                row = {'Age': None, 'PP0to5': None, 'PP6to11': None, 'PP12to23': None, 'NonPP': None, 'Pregnant': None, 'Parity': None}
                row['Age'] = int(round(person.age))
                row['NonPP'] = 1 if not person.postpartum else 0
                if person.postpartum:
                    row['PP0to5'] = 1 if 0 <= person.postpartum_dur < 6 else 0
                    row['PP6to11'] = 1 if 6 <= person.postpartum_dur < 12 else 0
                    row['PP12to23'] = 1 if 12 <= person.postpartum_dur <= 24 else 0
                row['Currently pregnant'] = 1 if person.pregnant else 0
                row['Parity'] = person.parity
                rows.append(row)

        pp = pd.DataFrame(rows, index = None, columns = ['Age', 'PP0to5', 'PP6to11', 'PP12to23', 'NonPP', 'Pregnant', 'Parity'])
        pp.fillna(0, inplace=True)
        return pp


    def plot(self, dosave=None, figargs=None, plotargs=None, axisargs=None, as_years=True):
        '''
        Plot the results -- can supply arguments for both the figure and the plots.

        Parameters
        ----------
        dosave : bool or str
            Whether or not to save the figure. If a string, save to that filename.

        figargs : dict
            Dictionary of kwargs to be passed to pl.figure()

        plotargs : dict
            Dictionary of kwargs to be passed to pl.plot()

        as_years : bool
            Whether to plot the x-axis as years or time points

        Returns
        -------
        Figure handle

        '''

        if figargs  is None: figargs  = {'figsize':(20,8)}
        if plotargs is None: plotargs = {'lw':2, 'alpha':0.7, 'marker':'o'}
        if axisargs is None: axisargs = {'left':0.1, 'bottom':0.05, 'right':0.9, 'top':0.97, 'wspace':0.2, 'hspace':0.25}

        fig = pl.figure(**figargs)
        pl.subplots_adjust(**axisargs)

        res = self.results # Shorten since heavily used

        x = res['t'] # Likewise
        if not as_years:
            x *= mpy
            x -= x[0]
            timelabel = 'Timestep'
        else:
            timelabel = 'Year'

        # Plot everything
        to_plot = sc.odict({
            'Population size': sc.odict({'pop_size':'Population size'}),
            'MCPR': sc.odict({'mcpr':'Modern contraceptive prevalence rate (%)'}),
            'Births and deaths': sc.odict({'births':'Births', 'deaths':'Deaths'}),
            'Birth-related mortality': sc.odict({'maternal_deaths':'Cumulative birth-related maternal deaths', 'infant_deaths':'Cumulative infant deaths'}),
            })
        for p,title,keylabels in to_plot.enumitems():
            pl.subplot(2,2,p+1)
            for i,key,label in keylabels.enumitems():
                if label.startswith('Cumulative'):
                    y = pl.cumsum(res[key])
                elif key == 'mcpr':
                    y = res[key]*100
                else:
                    y = res[key]
                pl.plot(x, y, label=label, **plotargs)
                if key == 'pop_size':
                    pl.scatter(self.pars['pop_years'], self.pars['pop_size'], **plotargs)
            utils.fixaxis(useSI=useSI)
            if key == 'mcpr':
                pl.ylabel('Percentage')
            else:
                pl.ylabel('Count')
            pl.xlabel(timelabel)
            pl.title(title, fontweight='bold')

        # Ensure the figure actually renders or saves
        if dosave:
            if isinstance(dosave, str):
                filename = dosave # It's a string, assume it's a filename
            else:
                filename = 'voi_sim.png' # Just give it a default name
            pl.savefig(filename)
        else:
            pl.show() # Only show if we're not saving

        return fig


    def plot_people(self):
        ''' Use imshow() to show all individuals as rows, with time as columns, one pixel per timestep per person '''
        raise NotImplementedError


def single_run(sim):
    sim.run()
    return sim

def multi_run(orig_sim, n=4, verbose=None):

    raise NotImplementedError('Memory leak, do not use!')

    # Copy the simulations
    sims = []
    for i in range(n):
        new_sim = sc.dcp(orig_sim)
        new_sim.pars['seed'] += i # Reset the seed, otherwise no point!
        new_sim.pars['n'] = int(new_sim.pars['n']/n) # Reduce the population size accordingly
        sims.append(new_sim)

    finished_sims = sc.parallelize(single_run, iterarg=sims)

    output_sim = sc.dcp(finished_sims[0])
    output_sim.pars['parallelized'] = n # Store how this was parallelized
    output_sim.pars['n'] *= n # Restore this since used in later calculations -- a bit hacky, it's true

    for sim in finished_sims[1:]: # Skip the first one
        output_sim.people.update(sim.people)
        for key,val in sim.results.items():
            if key != 't':
                output_sim.results[key] += sim.results[key]

    return output_sim

