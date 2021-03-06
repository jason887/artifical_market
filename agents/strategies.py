from random import uniform, gauss
from math import exp, sqrt
from keras.models import Model as MLModel
from keras.layers import Dense, Input
import numpy as np

class Strategy:
    """ Base class for strategies. """
    def __init__(self, agent, risk_aversion, loss_aversion, behaviour):
        self.strat_name = "Base"
        self.agent = agent
        self.model = agent.model
        self.stock = self.model.stock
        self.sigma_sq = self.stock.dividend_vol ** 2
        self.exp_p_d = self.stock.price + self.stock.dividend
        self.tolerance = 0.5 # profit slip tolerance
        self.neigh_exps = []
        self.interact_rate = self.model.glob_interact_rate
        if behaviour[0] == 'R':
            self.risk_aversion = risk_aversion
            self.loss_aversion = 1
            self.cond_loss_aversion = 1
            self.confidence = 1
        elif behaviour[0] == 'L':
            self.risk_aversion = 1
            self.loss_aversion = loss_aversion
            self.cond_loss_aversion = loss_aversion
            self.confidence = 1
        if len(behaviour) == 2:
            if behaviour[1] == "C": self.confidence = self.model.confidance_levels[0]
            elif behaviour[1] == "O": self.confidence = self.model.confidance_levels[1]
        self.prev_wealth = agent.wealth

    def calc_exp_p_d(self):
        pass

    def update_cond_loss_aversion(self):
        if self.prev_wealth > self.agent.wealth: self.cond_loss_aversion = self.loss_aversion
        else: self.cond_loss_aversion = 1
        self.prev_wealth = self.agent.wealth

    def calc_share_demand(self, stock=None): # stock argument to be used in multi stock simulation
        if self.loss_aversion != 1: self.update_cond_loss_aversion()
        return (self.exp_p_d - (1 + self.model.rf_rate) * self.stock.price) / \
                       (self.risk_aversion * self.cond_loss_aversion * self.confidence * self.sigma_sq)

    def calc_limit(self, stock=None):
        if self.model.settle_type == 'limit':
            limit_price = self.tolerance * self.exp_p_d \
                          + (1 - self.tolerance) * (1 + self.model.rf_rate)**self.model.dt * self.stock.price
        else:# self.model.settle_type == 'market':
            limit_price = None
        return limit_price

    def collect_neigh_exp(self):
        if self.model.current_step == 0: # drop condition if network not static
            self.agent.neighbors = [self.model.net.nodes[node]['agent_id'] for node
                                    in list(self.model.net.adj[self.agent.node])]
        exps = [self.model.schedule.agents[id].exp_p_d for id in self.agent.neighbors]
        self.neigh_exps = [x for x in exps if x is not None]

    def incorp_neighbour_exp(self):
        alpha = self.interact_rate
        neigh_average_exp = sum(self.neigh_exps) / len(self.neigh_exps)
        self.exp_p_d = (1 - alpha) * self.exp_p_d + alpha * neigh_average_exp
        return self.exp_p_d

class ZeroInformation(Strategy):
    def __init__(self, agent, risk_aversion, loss_aversion, behaviour):
        super().__init__(agent, risk_aversion, loss_aversion, behaviour)
        self.strat_name = "zero_information"

    def calc_exp_p_d(self):
        self.exp_p_d = 0.9 * self.exp_p_d + 0.1 * (uniform(0.98, 1.02) * (self.stock.price
                                                                                 + self.stock.dividend))
        return self.exp_p_d


class Value(Strategy):
    def __init__(self, agent, risk_aversion, loss_aversion, behaviour):
        super().__init__(agent, risk_aversion, loss_aversion, behaviour)
        self.strat_name = "value"
        self.div_noise_sig = uniform(0.05, 0.15)
        self.prev_dividend = self.stock.dividend

    def calc_exp_p_d(self):
        if self.model.current_step == 0 or self.prev_dividend != self.stock.dividend:
            # exp_d = self.stock.dividend * exp((self.stock.dividend_growth
            #                     - 0.5 * (self.stock.dividend_vol ** 2)) * (self.model.dt)
            #                     + self.stock.dividend_vol * sqrt(self.model.dt) * gauss(0, self.div_noise_sig))
            exp_d = self.stock.dividend * exp((self.stock.dividend_growth
                                - 0.5 * (self.stock.dividend_vol ** 2)) * (1/self.model.stock.dividend_freq)
                                + self.stock.dividend_vol * sqrt(1/self.model.stock.dividend_freq)
                                * gauss(0, self.div_noise_sig))
            self.prev_dividend = self.stock.dividend
            self.exp_p_d = exp_d / self.model.rf_rate + exp_d
        else: pass
        return self.exp_p_d


class Momentum(Strategy):
    def __init__(self, agent, risk_aversion, loss_aversion, behaviour):
        super().__init__(agent, risk_aversion, loss_aversion, behaviour)
        self.strat_name = "momentum"
        self.prev_p_d = self.stock.price + self.stock.dividend

    def calc_exp_p_d(self):
        phi = uniform(0, 0.02)
        curr_p_d = self.stock.price + self.stock.dividend
        if curr_p_d == self.prev_p_d: self.exp_p_d = self.stock.price + self.stock.dividend
        elif curr_p_d > self.prev_p_d: self.exp_p_d = (self.stock.price + self.stock.dividend) * (1 + phi)
        elif curr_p_d < self.prev_p_d: self.exp_p_d = (self.stock.price + self.stock.dividend) * (1 - phi)
        self.prev_p_d = self.stock.price + self.stock.dividend
        return self.exp_p_d

class ML(Strategy):
    def __init__(self, agent, risk_aversion, loss_aversion, behaviour):
        super().__init__(agent, risk_aversion, loss_aversion, behaviour)
        self.strat_name = "ml"
        self.lookback = 10
        self.hist_cutoff = 250
        self.train_freq = 50
        self.agent.halt_trade = True

        layer_width = 5
        activation = 'relu'
        optimizer = 'rmsprop'
        loss_func = 'mean_squared_error'
        x_in = Input(shape=(10,))
        h1 = Dense(layer_width, activation=activation)(x_in)
        h2 = Dense(layer_width, activation=activation)(h1)
        h3 = Dense(layer_width, activation=activation)(h2)
        y_hat = Dense(1, activation='linear')(h3)
        self.ml_model = MLModel(inputs=x_in, outputs=y_hat)
        self.ml_model.compile(optimizer=optimizer, loss=loss_func)

    def train_ml(self):
        rets = self.model.datacollector.model_vars["Return"][-self.hist_cutoff-1:]
        inp = []
        outp = []
        for i in range(len(rets)-self.lookback):
            x = rets[i:i+self.lookback]
            inp.append(x)
            y = rets[self.lookback]
            outp.append(y)
        input = np.array([inp])
        output = np.array([outp])
        self.ml_model.fit(input, output, epochs=1)

    def calc_exp_p_d(self):
        if self.model.current_step == self.lookback + self.hist_cutoff:
            self.agent.halt_trade = False
            self.train_ml()
            lookback_set = np.array(self.model.datacollector.model_vars["Return"][-self.lookback-1:])
            exp_ret = self.ml_model.predict(lookback_set)
            self.exp_p_d = self.stock.price * exp_ret
            return self.exp_p_d
        elif self.model.current_step > self.lookback + self.hist_cutoff:
            if self.model.current_step % self.train_freq == 0:
                self.train_ml()
            lookback_set = np.array(self.model.datacollector.model_vars["Return"][-self.lookback-1:])
            exp_ret = self.ml_model.predict(lookback_set)
            self.exp_p_d = self.stock.price * exp_ret
            return self.exp_p_d
        else:
            return None

