# ----------------------------------------
# LSM without STDP for KTH test
# add neurons to readout layer for multi-classification(one-versus-the-rest)
# using softmax(logistic regression)
# input layer is changed to 781*1 with encoding method
# change the LSM structure according to Maass paper
# new calculate flow as Maass_ST
# simplify the coding method with only extend the rank
# for the BO in parallel run
# with large scale
# combing CMA-ES optimize acquisition function
# add LHS to pre-build BO
# ----------------------------------------

from brian2 import *
from brian2tools import *
import scipy as sp
from scipy import stats
import struct
import matplotlib.pyplot as plt
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import accuracy_score
import pickle
from bqplot import *
import ipywidgets as widgets
import warnings
import os
import cv2
import re
from multiprocessing import Pool
import cma
import bayes_opt
from bayes_opt.event import Events
from bayes_opt.util import UtilityFunction
from functools import partial

from numpy import asarray, zeros, zeros_like, tile, array, argmin, mod
from numpy.random import random, randint, rand, seed as rseed, uniform


warnings.filterwarnings("ignore")
prefs.codegen.target = "numpy"
start_scope()
np.random.seed(100)
data_path = '../../../Data/KTH/'


# ------define general function------------
def wrap(v, vmin, vmax):
    w = vmax - vmin
    return vmin + mod(asarray(v) - vmin, w)


def evolve_population(pop, pop2, bound, f, c):
    npop, ndim = pop.shape

    for i in range(npop):

        # --- Vector selection ---
        v1, v2, v3 = i, i, i
        while v1 == i:
            v1 = randint(npop)
        while (v2 == i) or (v2 == v1):
            v2 = randint(npop)
        while (v3 == i) or (v3 == v2) or (v3 == v1):
            v3 = randint(npop)

        # --- Mutation ---
        v = pop[v1] + f * (pop[v2] - pop[v3])
        # random choice a value between when the solution out of the bounds
        for a, b in zip(enumerate(v), bound):
            if a[1] > b[1] or a[1] < b[0]:
                v[a[0]] = np.random.uniform(b[0], b[1], 1)

        # --- Cross over ---
        co = rand(ndim)
        for j in range(ndim):
            if co[j] <= c:
                pop2[i, j] = v[j]
            else:
                pop2[i, j] = pop[i, j]

        # --- Forced crossing ---
        j = randint(ndim)
        pop2[i, j] = v[j]

    return pop2


class DiffEvol(object):

    def __init__(self, fun, bounds, npop, f=None, c=None, seed=None, maximize=False, vectorize=False, cbounds=(0.25, 1),
                 fbounds=(0.25, 0.75), pool=None, min_ptp=1e-2, args=[], kwargs={}):
        if seed is not None:
            rseed(seed)

        self.minfun = _function_wrapper(fun, args, kwargs)
        self.bounds = asarray(bounds)
        self.n_pop = npop
        self.n_par = self.bounds.shape[0]
        self.bl = tile(self.bounds[:, 0], [npop, 1])
        self.bw = tile(self.bounds[:, 1] - self.bounds[:, 0], [npop, 1])
        self.m = -1 if maximize else 1
        self.pool = pool
        self.args = args

        if self.pool is not None:
            self.map = self.pool.map
        else:
            self.map = map

        self.periodic = []
        self.min_ptp = min_ptp

        self.cmin = cbounds[0]
        self.cmax = cbounds[1]
        self.cbounds = cbounds
        self.fbounds = fbounds

        self.seed = seed
        self.f = f
        self.c = c

        self._population = asarray(self.bl + random([self.n_pop, self.n_par]) * self.bw)
        self._fitness = zeros(npop)
        self._minidx = None

        self._trial_pop = zeros_like(self._population)
        self._trial_fit = zeros_like(self._fitness)

        if vectorize:
            self._eval = self._eval_vfun
        else:
            self._eval = self._eval_sfun

    @property
    def population(self):
        """The parameter vector population"""
        return self._population

    @property
    def minimum_value(self):
        """The best-fit value of the optimized function"""
        return self._fitness[self._minidx]

    @property
    def minimum_location(self):
        """The best-fit solution"""
        return self._population[self._minidx, :]

    @property
    def minimum_index(self):
        """Index of the best-fit solution"""
        return self._minidx

    def optimize(self, ngen):
        """Run the optimizer for ``ngen`` generations"""
        res = 0
        for res in self(ngen):
            pass
        return res

    def __call__(self, ngen=1):
        return self._eval(ngen)

    def _eval_sfun(self, ngen=1):
        """Run DE for a function that takes a single pv as an input and retuns a single value."""
        popc, fitc = self._population, self._fitness
        popt, fitt = self._trial_pop, self._trial_fit

        for ipop in range(self.n_pop):
            fitc[ipop] = self.m * self.minfun(popc[ipop, :])

        for igen in range(ngen):
            f = self.f or uniform(*self.fbounds)
            c = self.c or uniform(*self.cbounds)

            popt = evolve_population(popc, popt,self.bounds, f, c)
            fitt[:] = self.m * array(list(self.map(self.minfun, popt)))

            msk = fitt < fitc
            popc[msk, :] = popt[msk, :]
            fitc[msk] = fitt[msk]

            self._minidx = argmin(fitc)
            if fitc.ptp() < self.min_ptp:
                break

            yield popc[self._minidx, :], fitc[self._minidx]


class _function_wrapper(object):
    def __init__(self, f, args, kwargs):
        self.f = f
        self.args = args
        self.kwargs = kwargs

    def __call__(self, x):
        return self.f(x, *self.args, **self.kwargs)


class BayesianOptimization_(bayes_opt.BayesianOptimization):
    def __init__(self, f, pbounds, random_state=None, verbose=2):
        super(BayesianOptimization_, self).__init__(f, pbounds, random_state=None, verbose=2)

    def LHSample(self, N, bounds, D=None):
        if D == None:
            D = bounds.shape[0]
        result = np.empty([N, D])
        temp = np.empty([N])
        d = 1.0 / N
        for i in range(D):
            for j in range(N):
                temp[j] = np.random.uniform(
                    low=j * d, high=(j + 1) * d, size=1)[0]
            np.random.shuffle(temp)
            for j in range(N):
                result[j, i] = temp[j]
        lower_bounds = bounds[:, 0]
        upper_bounds = bounds[:, 1]
        if np.any(lower_bounds > upper_bounds):
            print('bounds error')
            return None
        np.add(np.multiply(result,
                           (upper_bounds - lower_bounds),
                           out=result),
               lower_bounds,
               out=result)
        return result

    def suggest(self, utility_function):
        if len(self._space) == 0:
            return self._space.array_to_params(self._space.random_sample())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._gp.fit(self._space.params, self._space.target)
        suggestion = self.acq_max_DE(
            ac=utility_function.utility,
            gp=self._gp,
            y_max=self._space.target.max(),
            bounds=self._space.bounds,
            random_state=self._random_state.randint(100000)
        )
        return self._space.array_to_params(suggestion)

    def acq_max_(self,ac, gp, y_max, bounds, random_state):
        x_seeds = random_state.uniform(bounds[:, 0], bounds[:, 1],
                                       size=(bounds.shape[0]))
        options = {'tolfunhist':-1e+4,'tolfun': -1e+4, 'ftarget': -1e+4, 'bounds': bounds.T.tolist(), 'maxiter': 1000,
                   'verb_log': 0,'verb_time':False,'verbose':-9}
        res = cma.fmin(lambda x: 1 - ac(x.reshape(1, -1), gp=gp, y_max=y_max), x_seeds, 0.25, options=options,
                       restarts=0, incpopsize=0, restart_from_best=False, bipop=False)
        x_max = res[0]
        return np.clip(x_max, bounds[:, 0], bounds[:, 1])

    def acq_max_DE(self, ac, gp, y_max, bounds, random_state, ngen=100, npop=45, f=0.4, c=0.3):
        de = DiffEvol(lambda x : 1 -ac(x.reshape(1, -1), gp=gp, y_max=y_max)[0], bounds, npop, f=f, c=c,
                      seed = random_state)
        de.optimize(ngen)
        print(de.minimum_value,de.minimum_location,de.minimum_index)
        x_max = de.minimum_location
        return np.clip(x_max, bounds[:, 0], bounds[:, 1])

    def _prime_queue_LHS(self, init_points):
        """Make sure there's something in the queue at the very beginning."""
        if self._queue.empty and self._space.empty:
            init_points = max(init_points, 1)
        LHS_points = self.LHSample(init_points, self._space.bounds)
        #         print(LHS_points)
        for point in LHS_points:
            self._queue.add(point)

    def maximize(self,
                  LHS_path = None,
                  init_points=5,
                  is_LHS=False,
                  n_iter=25,
                  acq='ucb',
                  kappa=2.576,
                  xi=0.0,
                  **gp_params):
         """Mazimize your function"""
         self._prime_subscriptions()
         self.dispatch(Events.OPTMIZATION_START)
         if LHS_path == None:
            if is_LHS:
                self._prime_queue_LHS(init_points)
            else:
                self._prime_queue(init_points)
         else:
            from bayes_opt.util import load_logs
            load_logs(self, logs=[LHS_path])
         self.set_gp_params(**gp_params)
         util = UtilityFunction(kind=acq, kappa=kappa, xi=xi)
         iteration = 0
         while not self._queue.empty or iteration < n_iter:
             try:
                 x_probe = next(self._queue)
             except StopIteration:
                 x_probe = self.suggest(util)
                 iteration += 1
             self.probe(x_probe, lazy=False)
         self.dispatch(Events.OPTMIZATION_END)


class Function():
    def __init__(self):
        pass

    def logistic(self, f):
        return 1 / (1 + np.exp(-f))

    def softmax(self, z):
        return np.array([(np.exp(i) / np.sum(np.exp(i))) for i in z])

    def gamma(self, a, size):
        return stats.gamma.rvs(a, size=size)


class Base():
    def update_states(self, type='pandas', *args, **kwargs):
        for seq, state in enumerate(kwargs):
            if type == 'pandas':
                kwargs[state] = kwargs[state].append(pd.DataFrame(args[seq]))
            elif type == 'numpy':
                kwargs[state] = self.np_extend(kwargs[state], args[seq], 1)
        return kwargs

    def normalization_min_max(self, arr):
        arr_n = arr
        for i in range(arr.size):
            x = float(arr[i] - np.min(arr)) / (np.max(arr) - np.min(arr))
            arr_n[i] = x
        return arr_n

    def mse(self, y_test, y):
        return sp.sqrt(sp.mean((y_test - y) ** 2))

    def classification(self, thea, data):
        data_n = self.normalization_min_max(data)
        data_class = []
        for a in data_n:
            if a >= thea:
                b = 1
            else:
                b = 0
            data_class.append(b)
        return np.asarray(data_class), data_n

    def allocate(self, G, X, Y, Z):
        V = np.zeros((X, Y, Z), [('x', float), ('y', float), ('z', float)])
        V['x'], V['y'], V['z'] = np.meshgrid(np.linspace(0, Y - 1, Y), np.linspace(0, X - 1, X),
                                             np.linspace(0, Z - 1, Z))
        V = V.reshape(X * Y * Z)
        np.random.shuffle(V)
        n = 0
        for g in G:
            for i in range(g.N):
                g.x[i], g.y[i], g.z[i] = V[n][0], V[n][1], V[n][2]
                n += 1
        return G

    def w_norm2(self, n_post, Synapsis):
        for i in range(n_post):
            a = Synapsis.w[np.where(Synapsis._synaptic_post == i)[0]]
            Synapsis.w[np.where(Synapsis._synaptic_post == i)[0]] = a / np.linalg.norm(a)

    def np_extend(self, a, b, axis=0):
        if a is None:
            shape = list(b.shape)
            shape[axis] = 0
            a = np.array([]).reshape(tuple(shape))
        return np.append(a, b, axis)

    def np_append(self, a, b):
        shape = list(b.shape)
        shape.insert(0, -1)
        if a is None:
            a = np.array([]).reshape(tuple(shape))
        return np.append(a, b.reshape(tuple(shape)), axis=0)

    def parameters_GS(self, *args, **kwargs):
        #---------------
        # args = [(min,max),]
        # kwargs = {'parameter' = number，}
        #---------------
        parameters = np.zeros(tuple(kwargs.values()), [(x, float) for x in kwargs.keys()])
        grids = np.meshgrid(*[np.linspace(min_max[0], min_max[1], scale)
                              for min_max,scale in zip(args,kwargs.values())], indexing='ij')
        for index, parameter in enumerate(kwargs.keys()):
            parameters[parameter] = grids[index]
        parameters = parameters.reshape(-1)
        return parameters

    def set_local_parameter_PS(self, S, parameter, boundary = None, method='random', **kwargs):
        if method == 'random':
            random = rand(S.N_post) * (boundary[1]-boundary[0]) + boundary[0]
            if '_post' in parameter:
                S.variables[parameter].set_value(random)
            else:
                S.variables[parameter].set_value(random[S.j])
        if method == 'group':
            try:
                group_n =  kwargs['group_parameters'].shape[0]
                n = int(np.floor(S.N_post / group_n))
                random = zeros(S.N_post)
                for i in range(group_n):
                    random[i * n:(i + 1) * n] = kwargs['group_parameters'][i]
                for j in range(S.N_post - group_n*n):
                    random[group_n * n + j:group_n * n + j + 1] = random[j * n]
            except KeyError:
                group_n = kwargs['group_n']
                n = int(np.floor(S.N_post / group_n))
                random = zeros(S.N_post)
                for i in range(group_n):
                    try:
                        random[i * n:(i + 1) * n] = rand() * (boundary[1]-boundary[0]) + boundary[0]
                    except IndexError:
                        random[i * n:] = rand() * (boundary[1]-boundary[0]) + boundary[0]
                        continue
            if '_post' in parameter:
                S.variables[parameter].set_value(random)
            else:
                S.variables[parameter].set_value(random[S.j])
        if method == 'location':
            group_n = kwargs['group_n']
            location_label = kwargs['location_label']
            random = zeros(S.N_post)
            bound = np.linspace(0, max(S.variables[location_label].get_value() + 1), num=group_n + 1)
            for i in range(group_n):
                random[(S.variables[location_label].get_value() >= bound[i]) & (
                            S.variables[location_label].get_value() < bound[i + 1])] \
                    = rand() * (boundary[1]-boundary[0]) + boundary[0]
            if '_post' in parameter:
                S.variables[parameter].set_value(random)
            else:
                S.variables[parameter].set_value(random[S.j])
        if method == 'in_coming':
            max_incoming = max(S.N_incoming)
            random = S.N_incoming / max_incoming * (boundary[1]-boundary[0]) + boundary[0]
            if '_post' in parameter:
                S.variables[parameter].set_value(random)
            else:
                S.variables[parameter].set_value(random[S.j])



class Readout():
    def readout_sk(self, X_train, X_test, y_train, y_test, **kwargs):
        from sklearn.linear_model import LogisticRegression
        lr = LogisticRegression(**kwargs)
        lr.fit(X_train.T, y_train.T)
        y_train_predictions = lr.predict(X_train.T)
        y_test_predictions = lr.predict(X_test.T)
        return accuracy_score(y_train_predictions, y_train.T), accuracy_score(y_test_predictions, y_test.T)

class Result():
    def __init__(self):
        pass

    def result_save(self, path, *arg, **kwarg):
        if os.path.exists(path):
            os.remove(path)
        fw = open(path, 'wb')
        pickle.dump(kwarg, fw)
        fw.close()

    def result_pick(self, path):
        fr = open(path, 'rb')
        data = pickle.load(fr)
        fr.close()
        return data

    def animation(self, t, v, interval, duration, a_step=10, a_interval=100, a_duration=10):
        xs = LinearScale()
        ys = LinearScale()
        line = Lines(x=t[:interval], y=v[:, :interval], scales={'x': xs, 'y': ys})
        xax = Axis(scale=xs, label='x', grid_lines='solid')
        yax = Axis(scale=ys, orientation='vertical', tick_format='0.2f', label='y', grid_lines='solid')
        fig = Figure(marks=[line], axes=[xax, yax], animation_duration=a_duration)

        def on_value_change(change):
            line.x = t[change['new']:interval + change['new']]
            line.y = v[:, change['new']:interval + change['new']]

        play = widgets.Play(
            interval=a_interval,
            value=0,
            min=0,
            max=duration,
            step=a_step,
            description="Press play",
            disabled=False
        )
        slider = widgets.IntSlider(min=0, max=duration)
        widgets.jslink((play, 'value'), (slider, 'value'))
        slider.observe(on_value_change, names='value')
        return play, slider, fig


class KTH_classification():
    def __init__(self):
        self.CATEGORIES = {
            "boxing": 0,
            "handclapping": 1,
            "handwaving": 2,
            "jogging": 3,
            "running": 4,
            "walking": 5
        }
        self.TRAIN_PEOPLE_ID = [11, 12, 13, 14, 15, 16, 17, 18]
        self.VALIDATION_PEOPLE_ID = [19, 20, 21, 23, 24, 25, 1, 4]
        self.TEST_PEOPLE_ID = [22, 2, 3, 5, 6, 7, 8, 9, 10]

    def parse_sequence_file(self, path):
        print("Parsing %s" % path)

        with open(path, 'r') as content_file:
            content = content_file.read()
        content = re.sub("[\t\n]", " ", content).split()
        self.frames_idx = {}
        current_filename = ""
        for s in content:
            if s == "frames":
                continue
            elif s.find("-") >= 0:
                if s[len(s) - 1] == ',':
                    s = s[:-1]
                idx = s.split("-")
                if current_filename[:6] == 'person':
                    if not current_filename in self.frames_idx:
                        self.frames_idx[current_filename] = []
                    self.frames_idx[current_filename].append([int(idx[0]), int(idx[1])])
            else:
                current_filename = s + "_uncomp.avi"

    def load_data_KTH(self, data_path, dataset="train"):
        if dataset == "train":
            ID = self.TRAIN_PEOPLE_ID
        elif dataset == "validation":
            ID = self.VALIDATION_PEOPLE_ID
        else:
            ID = self.TEST_PEOPLE_ID

        data = []
        for category in self.CATEGORIES.keys():
            folder_path = os.path.join(data_path, category)
            filenames = sorted(os.listdir(folder_path))
            for filename in filenames:
                filepath = os.path.join(data_path, category, filename)
                person_id = int(filename.split("_")[0][6:])
                if person_id not in ID:
                    continue
                condition_id = int(filename.split("_")[2][1:])
                cap = cv2.VideoCapture(filepath)
                for f_id, seg in enumerate(self.frames_idx[filename]):
                    frames = []
                    cap.set(cv2.CAP_PROP_POS_FRAMES, seg[0] - 1)  # 设置要获取的帧号
                    count = 0
                    while (cap.isOpened() and seg[0] + count - 1 < seg[1] + 1):
                        ret, frame = cap.read()
                        if ret == True:
                            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                            frames.append(gray.reshape(-1))
                            count += 1
                        else:
                            break
                    data.append({
                        "frames": np.array(frames),
                        "category": category,
                        "person_id": person_id,
                        "condition_id": condition_id,
                        "frame_id": f_id,
                    })
                cap.release()
        return pd.DataFrame(data)

    def frame_diff(self, frames, origin_size=(120, 160)):
        frame_diff = []
        it = frames.__iter__()
        frame_pre = next(it).reshape(origin_size)
        while True:
            try:
                frame = next(it).reshape(origin_size)
                frame_diff.append(cv2.absdiff(frame_pre, frame).reshape(-1))
                frame_pre = frame
            except StopIteration:
                break
        return np.asarray(frame_diff)

    def block_array(self, matrix, size):
        if int(matrix.shape[0] % size[0]) == 0 and int(matrix.shape[1] % size[1]) == 0:
            X = int(matrix.shape[0] / size[0])
            Y = int(matrix.shape[1] / size[1])
            shape = (X, Y, size[0], size[1])
            strides = matrix.itemsize * np.array([matrix.shape[1] * size[0], size[1], matrix.shape[1], 1])
            squares = np.lib.stride_tricks.as_strided(matrix, shape=shape, strides=strides)
            return squares
        else:
            raise ValueError('matrix must be divided by size exactly')

    def pooling(self, frames, origin_size=(120, 160), pool_size=(5, 5), types='max'):
        data = []
        for frame in frames:
            pool = np.zeros((int(origin_size[0] / pool_size[0]), int(origin_size[1] / pool_size[1])), dtype=np.int16)
            frame_block = self.block_array(frame.reshape(origin_size), pool_size)
            for i, row in enumerate(frame_block):
                for j, block in enumerate(row):
                    if types == 'max':
                        pool[i][j] = block.max()
                    elif types == 'average':
                        pool[i][j] = block.mean()
                    else:
                        raise ValueError('I have not done that type yet..')
            data.append(pool.reshape(-1))
        return np.asarray(data)

    def threshold_norm(self, frames, threshold):
        frames = (frames - np.min(frames)) / (np.max(frames) - np.min(frames))
        frames[frames < threshold] = 0
        frames[frames > threshold] = 1
        frames = frames.astype('<i1')
        return frames

    def load_data_KTH_all(self, data_path):
        self.parse_sequence_file(data_path+'00sequences.txt')
        self.train = self.load_data_KTH(data_path, dataset="train")
        self.validation = self.load_data_KTH(data_path, dataset="validation")
        self.test = self.load_data_KTH(data_path, dataset="test")

    def select_data_KTH(self, fraction, data_frame, is_order=True, **kwargs):
        try:
            selected = kwargs['selected']
        except KeyError:
            selected = self.CATEGORIES.keys()
        if is_order:
            data_frame_selected = data_frame[data_frame['category'].isin(selected)].sample(
                frac=fraction).sort_index().reset_index(drop=True)
        else:
            data_frame_selected = data_frame[data_frame['category'].isin(selected)].sample(frac=fraction).reset_index(
                drop=True)
        return data_frame_selected

    def encoding_latency_KTH(self, analog_data, origin_size=(120, 160), pool_size=(5, 5), types='max', threshold=0.2):
        data_diff = analog_data.frames.apply(self.frame_diff, origin_size=origin_size)
        data_diff_pool = data_diff.apply(self.pooling, origin_size=origin_size, pool_size = pool_size, types = types)
        data_diff_pool_threshold_norm = data_diff_pool.apply(self.threshold_norm, threshold=threshold)
        label = analog_data.category.map(self.CATEGORIES).astype('<i1')
        data_frame = pd.DataFrame({'value': data_diff_pool_threshold_norm, 'label': label})
        return data_frame

    def get_series_data_list(self, data_frame, is_group=False):
        data_frame_s = []
        if not is_group:
            for value in data_frame['value']:
                data_frame_s.extend(value)
        else:
            for value in data_frame['value']:
                data_frame_s.append(value)
        label = data_frame['label']
        return np.asarray(data_frame_s), label

    def dump_data(self, path, dataset):
        if os.path.exists(path):
            os.remove(path)
        with open(path, 'wb') as file:
            pickle.dump(dataset, file)

    def load_data(self, path):
        with open(path, 'rb') as file:
            return pickle.load(file)


###################################
# -----simulation parameter setting-------
LOAD_DATA = True
USE_VALIDATION = True

origin_size=(120, 160)
pool_size=(5, 5)
types='max'
threshold=0.2

F_train = 1
F_validation = 1
F_test = 1
Dt = defaultclock.dt = 1 * ms
standard_tau = 100

#-------class initialization----------------------
function = Function()
base = Base()
readout = Readout()
result = Result()
KTH = KTH_classification()

# -------data initialization----------------------
if LOAD_DATA:

    df_en_train = KTH.load_data(data_path + 'train.p')
    df_en_validation = KTH.load_data(data_path + 'validation.p')
    df_en_test = KTH.load_data(data_path + 'test.p')

else:

    KTH.load_Data_KTH_all(data_path)

    df_train = KTH.select_data_KTH(F_train, KTH.train, False)
    df_validation = KTH.select_data_KTH(F_validation, KTH.validation, False)
    df_test = KTH.select_data_KTH(F_train, KTH.test, False)

    df_en_train = KTH.encoding_latency_KTH(df_train, origin_size, pool_size, types, threshold)
    df_en_validation = KTH.encoding_latency_KTH(df_validation, origin_size, pool_size, types, threshold)
    df_en_test = KTH.encoding_latency_KTH(df_test, origin_size, pool_size, types, threshold)

    KTH.dump_data(data_path + 'train.p', df_en_train)
    KTH.dump_data(data_path + 'validation.p', df_en_validation)
    KTH.dump_data(data_path + 'test.p', df_en_test)

data_train_s, label_train = KTH.get_series_data_list(df_en_train, is_group=True)
data_validation_s, label_validation = KTH.get_series_data_list(df_en_validation, is_group=True)
data_test_s, label_test = KTH.get_series_data_list(df_en_test, is_group=True)

if USE_VALIDATION:

    data_train_s = base.np_extend(data_train_s, data_validation_s)
    label_train = base.np_extend(label_train, label_validation)

#-------get numpy random state------------
np_state = np.random.get_state()


############################################
# ---- define network run function----
def run_net(inputs, **parameter):
    #---- set numpy random state for each run----
    np.random.set_state(np_state)

    # -----parameter setting-------
    n_ex = 800
    n_inh = int(n_ex / 4)
    n_input = (origin_size[0] * origin_size[1]) / (pool_size[0] * pool_size[1])
    n_read = n_ex + n_inh

    R = parameter['R']*2
    f_in = parameter['f_in']
    f_EE = parameter['f_EE']
    f_EI = parameter['f_EI']
    f_IE = parameter['f_IE']
    f_II = parameter['f_II']

    A_EE = 60*f_EE
    A_EI = 60*f_EI
    A_IE = 60*f_IE
    A_II = 60*f_II
    A_inE = 60*f_in
    A_inI = 60*f_in

    tau_ex = parameter['tau_ex']*standard_tau
    tau_inh = parameter['tau_inh']*standard_tau
    tau_read= 30

    p_inE = parameter['p_in']*0.1
    p_inI = parameter['p_in']*0.1

    #------definition of equation-------------
    neuron_in = '''
    I = stimulus(t,i) : 1
    '''

    neuron = '''
    tau : 1
    dv/dt = (I-v) / (tau*ms) : 1 (unless refractory)
    dg/dt = (-g)/(3*ms) : 1
    dh/dt = (-h)/(6*ms) : 1
    I = (g+h)+13.5: 1
    x : 1
    y : 1
    z : 1
    '''

    neuron_read = '''
    tau : 1
    dv/dt = (I-v) / (tau*ms) : 1
    dg/dt = (-g)/(3*ms) : 1 
    dh/dt = (-h)/(6*ms) : 1
    I = (g+h): 1
    '''

    synapse = '''
    w : 1
    '''

    on_pre_ex = '''
    g+=w
    '''

    on_pre_inh = '''
    h-=w
    '''

    # -----Neurons and Synapses setting-------
    Input = NeuronGroup(n_input, neuron_in, threshold='I > 0', method='euler', refractory=0 * ms,
                        name = 'neurongroup_input')

    G_ex = NeuronGroup(n_ex, neuron, threshold='v > 15', reset='v = 13.5', method='euler', refractory=3 * ms,
                    name ='neurongroup_ex')

    G_inh = NeuronGroup(n_inh, neuron, threshold='v > 15', reset='v = 13.5', method='euler', refractory=2 * ms,
                    name ='neurongroup_in')

    G_readout = NeuronGroup(n_read, neuron_read, method='euler', name='neurongroup_read')

    S_inE = Synapses(Input, G_ex, synapse, on_pre = on_pre_ex ,method='euler', name='synapses_inE')

    S_inI = Synapses(Input, G_inh, synapse, on_pre = on_pre_ex ,method='euler', name='synapses_inI')

    S_EE = Synapses(G_ex, G_ex, synapse, on_pre = on_pre_ex ,method='euler', name='synapses_EE')

    S_EI = Synapses(G_ex, G_inh, synapse, on_pre = on_pre_ex ,method='euler', name='synapses_EI')

    S_IE = Synapses(G_inh, G_ex, synapse, on_pre = on_pre_inh ,method='euler', name='synapses_IE')

    S_II = Synapses(G_inh, G_inh, synapse, on_pre = on_pre_inh ,method='euler', name='synapses_I')

    S_E_readout = Synapses(G_ex, G_readout, 'w = 1 : 1', on_pre=on_pre_ex, method='euler')

    S_I_readout = Synapses(G_inh, G_readout, 'w = 1 : 1', on_pre=on_pre_inh, method='euler')

    #-------initialization of neuron parameters----------
    G_ex.v = '13.5+1.5*rand()'
    G_inh.v = '13.5+1.5*rand()'
    G_readout.v = '0'
    G_ex.g = '0'
    G_inh.g = '0'
    G_readout.g = '0'
    G_ex.h = '0'
    G_inh.h = '0'
    G_readout.h = '0'
    G_ex.tau = tau_ex
    G_inh.tau = tau_inh
    G_readout.tau = tau_read

    [G_ex,G_in] = base.allocate([G_ex,G_inh],5,10,20)

    # -------initialization of network topology and synapses parameters----------
    S_inE.connect(condition='j<0.3*N_post', p = p_inE)
    S_inI.connect(condition='j<0.3*N_post', p = p_inI)
    S_EE.connect(condition='i != j', p='0.3*exp(-((x_pre-x_post)**2+(y_pre-y_post)**2+(z_pre-z_post)**2)/R**2)')
    S_EI.connect(p='0.2*exp(-((x_pre-x_post)**2+(y_pre-y_post)**2+(z_pre-z_post)**2)/R**2)')
    S_IE.connect(p='0.4*exp(-((x_pre-x_post)**2+(y_pre-y_post)**2+(z_pre-z_post)**2)/R**2)')
    S_II.connect(condition='i != j', p='0.1*exp(-((x_pre-x_post)**2+(y_pre-y_post)**2+(z_pre-z_post)**2)/R**2)')
    S_E_readout.connect(j='i')
    S_I_readout.connect(j='i+n_ex')

    S_inE.w = function.gamma(A_inE, S_inE.w.shape)
    S_inI.w = function.gamma(A_inI, S_inI.w.shape)
    S_EE.w = function.gamma(A_EE, S_EE.w.shape)
    S_IE.w = function.gamma(A_IE, S_IE.w.shape)
    S_EI.w = function.gamma(A_EI, S_EI.w.shape)
    S_II.w = function.gamma(A_II, S_II.w.shape)

    S_EE.pre.delay = '1.5*ms'
    S_EI.pre.delay = '0.8*ms'
    S_IE.pre.delay = '0.8*ms'
    S_II.pre.delay = '0.8*ms'

    # ------create network-------------
    net = Network(collect())
    net.store('init')

    # ------run network-------------
    stimulus = TimedArray(inputs[0], dt=Dt)
    duration = inputs[0].shape[0]
    net.run(duration * Dt)
    states = net.get_states()['neurongroup_read']['v']
    net.restore('init')
    return (states, inputs[1])


def parameters_search(**parameter):
     # ------parallel run for train-------
    states_train_list = pool.map(partial(run_net, **parameter), [(x) for x in zip(data_train_s, label_train)])
    # ----parallel run for test--------
    states_test_list = pool.map(partial(run_net, **parameter), [(x) for x in zip(data_test_s, label_test)])
    # ------Readout---------------
    states_train, states_test, _label_train, _label_test = [], [], [], []
    for train in states_train_list :
        states_train.append(train[0])
        _label_train.append(train[1])
    for test in states_test_list:
        states_test.append(test[0])
        _label_test.append(test[1])
    states_train = (MinMaxScaler().fit_transform(np.asarray(states_train))).T
    states_test = (MinMaxScaler().fit_transform(np.asarray(states_test))).T
    score_train, score_test = readout.readout_sk(states_train, states_test,
                                                 np.asarray(_label_train), np.asarray(_label_test),
                                                 solver="lbfgs", multi_class="multinomial")
    # ----------show results-----------
    print('parameters %s' % parameter)
    print('Train score: ', score_train)
    print('Test score: ', score_test)
    return score_test

##########################################
# -------BO parameters search---------------
if __name__ == '__main__':
    core = 10
    pool = Pool(core)

    optimizer = BayesianOptimization_(
        f=parameters_search,
        pbounds= {'R': (0.0001, 1), 'p_in': (0.0001, 1), 'f_in': (0.0001, 1), 'f_EE': (0.0001, 1), 'f_EI': (0.0001, 1),
               'f_IE': (0.0001, 1), 'f_II': (0.0001, 1), 'tau_ex': (0.0001, 1), 'tau_inh': (0.0001, 1)},
        verbose=2,
        random_state=np.random.RandomState(),
    )

    logger = bayes_opt.observer.JSONLogger(path="./BO_res_KTH.json")
    optimizer.subscribe(bayes_opt.event.Events.OPTMIZATION_STEP, logger)

    optimizer.maximize(
        LHS_path='./LHS.json',
        init_points=50,
        is_LHS = True,
        n_iter=250,
        acq='ei',
        kappa=2.576,
        xi=0.0,
    )