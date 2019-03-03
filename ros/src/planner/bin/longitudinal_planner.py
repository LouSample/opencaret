#!/usr/bin/python
import cvxpy as cvx
from util import util
from std_msgs.msg import Float32
from sensor_msgs.msg import Imu
from opencaret_msgs.msg import Obstacle, LongitudinalPlan
import time
import math
import numpy as np
import rospy
INITIAL_CRUISING_SPEED = 5.0
INITIAL_DISTANCE_TO_LEAD_CAR = 100.0

RATE = 4.0

class LongitudinalPlanner():

    def __init__(self):
        self.T = 20
        self.dt = 0.2
        self.min_follow_distance = cvx.Parameter(value=6.0)
        self.max_acceleration = cvx.Parameter(value=5.0)
        self.min_acceleration = cvx.Parameter(value=-5.0)
        self.min_max_jerk = cvx.Parameter(value=3.0)

        self.cruising_speed = cvx.Parameter(value=util.mph_to_ms(INITIAL_CRUISING_SPEED))
        self.last_v_trajectory = None
        self.last_a_trajectory = None
        self.last_x_trajectory = None
        self.v_ego = cvx.Parameter(value=0.0)
        self.a_ego = cvx.Parameter(value=0.0)
        self.x_lead = cvx.Parameter(value=INITIAL_DISTANCE_TO_LEAD_CAR)
        self.v_lead = cvx.Parameter(value=self.cruising_speed.value)
        self.a_lead = cvx.Parameter(value=0.0)

        self.v = cvx.Variable(self.T + 1)
        self.a = cvx.Variable(self.T + 1)
        self.j = cvx.Variable(self.T)
        self.x = cvx.Variable(self.T + 1)

        self.solver = self.init_mpc_solver()
        self.cruising_speed_sub = rospy.Subscriber('cruising_speed', Float32, self.on_cruising_speed)
        self.speed_sub = rospy.Subscriber('wheel_speed', Float32, self.on_wheel_speed)
        self.lead_car_sub = rospy.Subscriber('lead_obstacle', Obstacle, self.on_lead_obstacle)
        self.computed_accel_sub = rospy.Subscriber('computed_accel_filtered', Float32, self.on_computed_accel)

        self.plan_pub = rospy.Publisher('longitudinal_plan', LongitudinalPlan, queue_size=1)
        rate = rospy.Rate(RATE)
        while not rospy.is_shutdown():
            self.make_plan()
            rate.sleep()

    def on_cruising_speed(self, msg):
        self.cruising_speed.value = msg.data

    def on_wheel_speed(self, msg):
        self.v_ego.value = msg.data

    def on_computed_accel(self, msg):
        self.a_ego.value = msg.data

    def on_lead_obstacle(self, msg):
        self.x_lead.value = min(INITIAL_DISTANCE_TO_LEAD_CAR, msg.point.x)
        self.v_lead.value= msg.relative_speed + float(self.v_ego.value)
        self.a_lead.value = 0

    def on_imu(self, msg):
        self.a_ego.value = msg.linear_acceleration.x

    def init_mpc_solver(self):
        states = []
        for t in range(self.T):
            cost = cvx.sum_squares(self.v[t + 1] - self.v_lead - self.a_lead * self.dt) + cvx.sum_squares(self.j[t]) * 5 + \
                   cvx.sum_squares(self.min_follow_distance - self.x[t + 1]) * 5 + cvx.sum_squares(self.a[t + 1])
            # if t > 0:
            #     cost += cvx.sum_squares(a[t] - a[t - 1]) * 10

            #  Setup constraints for a basic kinematic model of the car assuming constant velocity
            #  of the lead car for the time being

            constr = [self.v[t + 1] == self.v[t] + self.a[t] * self.dt,
                      self.x[t + 1] == self.x[t] - self.v[t] * self.dt + self.v_lead * self.dt,
                      self.x[t + 1] >= self.min_follow_distance - 3,
                      self.v[t + 1] <= self.cruising_speed + 1,
                      self.a[t + 1] <= self.max_acceleration,
                      self.a[t + 1] >= self.min_acceleration,
                      self.v[t + 1] >= 0.0
                      ]

            if t > 0:
                constr += [self.j[t] == (self.a[t] - self.a[t - 1]) / self.dt,
                           self.j[t] <= self.min_max_jerk,
                           self.j[t] >= -self.min_max_jerk]

            prob = cvx.Problem(cvx.Minimize(cost), constr)
            if not prob.is_dcp():
                print("not dcp at t = {}".format(t))
                exit()
            states.append(prob)
        # sums problem objectives and concatenates constraints.
        prob = sum(states)
        constraints = [self.v[0] == self.v_ego,
                       self.a[0] == self.a_ego,
                       self.x[0] == self.x_lead] + prob.constraints
        return cvx.Problem(prob.objective, constraints=constraints)

    def make_plan(self):

        start = time.time()
        if not math.isinf(self.solver.solve()):
            self.last_v_trajectory = self.x.value
            self.last_a_trajectory = self.a.value
            self.last_x_trajectory = self.x.value
            plan = LongitudinalPlan()
            plan.dt = [float(t * self.dt) for t in range(self.T)]
            # print(util.ms_to_mph(np.array(self.v.value).reshape(-1)[5]))
            plan.distance_from_lead = np.array(self.x.value).reshape(-1).tolist()
            plan.accel = np.array(self.a.value).reshape(-1).tolist()
            plan.velocity = np.array(self.v.value).reshape(-1).tolist()
            self.plan_pub.publish(plan)
        else:
            rospy.logerr("No solution with v: {}, a: {}, x: {}".format(self.v_ego.value, self.a_ego.value, self.x_lead.value))


def main():
    rospy.init_node('planner', anonymous=False, log_level=rospy.DEBUG)
    # Get the parameters for the LLC node.
    LongitudinalPlanner()


if __name__ == '__main__':
    main()
